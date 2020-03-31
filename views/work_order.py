#-*- coding: utf-8 -*-
import redis
import time
import os
import json
import oss2
from collections import defaultdict
from flask_mail import Mail
from flask_mail import Message
from flask import Blueprint,render_template,request,g,Flask
from module import user_auth,db_op,loging,MyForm,tools,Md5
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import and_,desc,distinct,or_
app = Flask(__name__)
mail = Mail(app)
DB = SQLAlchemy(app)
app.config.from_pyfile('../conf/redis.conf')
app.config.from_pyfile('../conf/mail.conf')
app.config.from_pyfile('../conf/oss.conf')
app.config.from_pyfile('../conf/tokens.conf')
app.config.from_pyfile('../conf/work_order.conf')
logging = loging.Error()
redis_host = app.config.get('REDIS_HOST')
redis_port = app.config.get('REDIS_PORT')
redis_password = app.config.get('REDIS_PASSWORD')
Redis = redis.StrictRedis(host=redis_host, port=redis_port,decode_responses=True)
sender = app.config.get('MAIL_DEFAULT_SENDER')
receiver = app.config.get('DEFAULT_RECEIVER')
test_mail = app.config.get('TEST_MAIL')
work_token = app.config.get('WORK_TOKEN')
ALLOWED_EXTENSIONS = set(['zip'])
oss_id = app.config.get('OSS_ID')
oss_key = app.config.get('OSS_KEY')
oss_url = app.config.get('OSS_URL')
server_auth_leader = app.config.get('SAL')
source_types = app.config.get('SOURCE_TYPES')
work_types = app.config.get('WORK_TYPES')
order_types = app.config.get('ORDER_TYPES')
page_work_order = Blueprint('work_order',__name__)
def check_mail(mail):
    db_sso = db_op.user_sso
    try:
        val = db_sso.query.filter(db_sso.mail==mail).all()
        if val:
            return True
    except Exception as e:
        logging.error(e)
    return False

@page_work_order.route('/work_comment/<int:work_number>')
def work_comment(work_number=None):
    comments = []
    db_work_order = db_op.work_order
    work_stats = db_work_order.query.with_entities(db_work_order.id).filter(and_(
        db_work_order.status.in_(('未受理', '未审核', '受理中', '待审批', '审批通过')),
        db_work_order.work_number == work_number)).all()
    if work_number:
        db_work_comment = db_op.work_comment
        comments = db_work_comment.query.with_entities(db_work_comment.date_time,db_work_comment.user,
                                                   db_work_comment.comment).filter(
            db_work_comment.work_number==work_number).order_by(db_work_comment.date_time).all()
        if comments:
            comments = ['%s %s:%s'%comment for comment in comments]
        #清除该工单问题备注通知信息
        Redis.hdel('op_work_comment_alarm_%s' % g.dingId,work_number)
    return render_template('work_comment.html',comments=comments,work_number=work_number,work_stats=work_stats)

@page_work_order.route('/work_order')
def work_order():
    work_orders = {'application':'代码上线工单',
                   'server_auth':'服务器权限工单',
                   'sql_execute':'SQL执行工单',
                   'project_offline':'项目下线工单',
                   'other_work':'其它事项工单'
                   }
    work_order_lists = {'work_order_show': '工单进度查询',
                        'work_repeal': '撤销工单申请',
                        'work_review': '工单申请审核'
                        }
    return render_template('op_front.html',work_orders=work_orders,work_order_lists=work_order_lists)

@page_work_order.route('/work_norun')
@page_work_order.route('/work_norun/<self>')
def work_norun(self=None):
    try:
        # 未关闭工单展示
        db_work_order = db_op.work_order
        db_sso = db_op.user_sso
        infos = db_sso.query.with_entities(db_sso.dingunionid, db_sso.realName,db_sso.mail).all()
        users = {info[0]: info[1] for info in infos}
        mails = {info[0]: info[-1] for info in infos}
        work_lists = db_work_order.query.with_entities(db_work_order.work_number,
                                                        db_work_order.date,
                                                        db_work_order.source,
                                                        db_work_order.applicant,
                                                        db_work_order.status).filter(
            db_work_order.status.in_(('未审核','未受理', '受理中', '待审批', '审批通过'))).order_by(desc(db_work_order.work_number)).all()
        if self:
            work_lists = db_work_order.query.with_entities(db_work_order.work_number,
                                                           db_work_order.date,
                                                           db_work_order.source,
                                                           db_work_order.applicant,
                                                           db_work_order.status).filter(and_(
                db_work_order.status.in_(('未审核', '受理中', '待审批')),or_(db_work_order.dingid==g.dingId,
                                                                    db_work_order.reviewer==mails[g.dingId],
                                                                    db_work_order.approval==g.dingId))).order_by(
                desc(db_work_order.work_number)).all()
        if work_lists:
            work_lists = [list(info) for info in work_lists]
            for info in work_lists:
                if info[3] in users:
                    info[3] = users[info[3]]
                else:
                    info[3] = ''
        if self:
            return render_template('ajax_work_norun.html', work_lists=work_lists, source_types=source_types)
        return render_template('work_norun.html', work_lists=work_lists, source_types=source_types)
    except Exception as e:
        logging.error(e)

@page_work_order.route('/work_repeal')
def work_repeal():
    try:
        # 撤销工单操作
        db_work_order = db_op.work_order
        db_sso = db_op.user_sso
        users = db_sso.query.with_entities(db_sso.dingunionid, db_sso.realName).all()
        users = {info[0]: info[1] for info in users}
        work_lists = db_work_order.query.with_entities(db_work_order.work_number,
                                                        db_work_order.date,
                                                        db_work_order.source,
                                                        db_work_order.applicant,
                                                        db_work_order.status).filter(and_(
            db_work_order.status.in_(('未审核','未受理', '已退回', '待审批', '审批拒绝','已拒绝')),
            db_work_order.applicant==g.dingId)).order_by(desc(db_work_order.work_number)).all()
        if work_lists:
            work_lists = [list(info) for info in work_lists]
            for info in work_lists:
                if info[3] in users:
                    info[3] = users[info[3]]
                else:
                    info[3] = ''
        return render_template('work_repeal.html', work_lists=work_lists,
                               source_types=source_types,work_types=work_types,order_types=order_types)
    except Exception as e:
        logging.error(e)

@page_work_order.route('/work_review')
@page_work_order.route('/work_review/<self>')
def work_review(self=None):
    try:
        # 需审核工单展示
        db_work_order = db_op.work_order
        db_sso = db_op.user_sso
        infos = db_sso.query.with_entities(db_sso.dingunionid, db_sso.realName,db_sso.mail).all()
        users = {info[0]: info[1] for info in infos}
        mails = {info[0]: info[-1] for info in infos}
        action = tools.http_args(request,'action')
        work_number = tools.http_args(request,'work_number')
        msg = None
        if action in ('review_pass','review_deny') and work_number:
            val = db_work_order.query.with_entities(db_work_order.applicant,db_work_order.source).filter(and_(db_work_order.work_number == int(work_number),
                                                  db_work_order.reviewer == g.mail,db_work_order.status=='未审核')).all()
            if val:
                try:
                    applicanter = mails[val[0][0]]
                    work_source = val[0][1]
                    if action == 'review_pass':
                        status = '未受理'
                        if work_source =='ensure_server_auth':
                            status = '待审批'
                        db_work_order.query.filter(db_work_order.work_number==int(work_number)).update({db_work_order.status:status})
                    else:
                        db_work_order.query.filter(db_work_order.work_number == int(work_number)).update(
                            {db_work_order.status: '已退回'})
                    db_op.DB.session.commit()
                except Exception as e:
                    logging.error(e)
                else:
                    #获取发送钉钉内容
                    text = Redis.get('op_send_dingding_msg_%s' % work_number)
                    text = eval(text)
                    #获取发送邮件内容
                    mail_html = Redis.get('op_send_mail_html_%s' % work_number)
                    #获取受理链接地址
                    ensure_url = Redis.get('op_send_ensure_url_%s' % work_number)
                    msg_url = Redis.get('op_send_msg_url_%s' % work_number)
                    try:
                        receiver = app.config.get('DEFAULT_RECEIVER')
                        if Redis.exists('op_other_work_receiver_%s' % work_number):
                            receiver = Redis.get('op_other_work_receiver_%s' % work_number)
                        cc_mail = [g.mail,applicanter]
                        if Redis.exists('op_cc_test_mail_%s' % work_number):
                            cc_mail.append(Redis.get('op_cc_test_mail_%s' % work_number))
                        msg = Message("%s运维工单进度通知" % work_number, sender=sender, recipients=[receiver],
                                      cc=cc_mail)
                        if action  == 'review_pass':
                            result = "部门审核通过"
                            if work_source =='ensure_server_auth':
                                ensure_url = ''
                                msg_url = ''
                                result = "部门审核通过,等待运维部门审核"
                            msg.html = '{0}<div>{1}</div><div>{2}</div>'.format(mail_html,'<p style="color:red">工单状态:%s</p>' %result,ensure_url)
                            text.append('#### 工单状态:%s' %result)
                            text.append(msg_url)
                        else:
                            msg.html = '%s%s' % (mail_html, '<p style="color:red">工单状态:工单被退回</p>')
                            text.append('#### 工单状态:工单被退回')
                        with app.app_context():
                            mail.send(msg)
                        # 发送钉钉
                        tools.dingding_msg(text, token=work_token)
                    except Exception as e:
                        logging.error(e)
                    else:
                        msg = "%s工单审核完成!" %work_number
            else:
                msg = "工单不需要审核!"
        if g.grade[0] == '0':
            work_lists = db_work_order.query.with_entities(db_work_order.work_number,
                                                           db_work_order.date,
                                                           db_work_order.source,
                                                           db_work_order.applicant,
                                                           db_work_order.status).filter(
                db_work_order.status == '未审核').order_by(desc(db_work_order.work_number)).all()
        else:
            work_lists = db_work_order.query.with_entities(db_work_order.work_number,
                                                        db_work_order.date,
                                                        db_work_order.source,
                                                        db_work_order.applicant,
                                                        db_work_order.status).filter(and_(db_work_order.reviewer == g.mail
            ,db_work_order.status =='未审核')).order_by(desc(db_work_order.work_number)).all()
        if work_lists:
            work_lists = [list(info) for info in work_lists]
            for info in work_lists:
                if info[3] in users:
                    info[3] = users[info[3]]
                else:
                    info[3] = ''
        if self:
            return render_template('ajax_work_review.html', work_lists=work_lists, msg=msg, source_types=source_types,
                                   order_types=order_types)
        return render_template('work_review.html', work_lists=work_lists, msg=msg, source_types=source_types,
                               order_types=order_types)
    except Exception as e:
        logging.error(e)

@page_work_order.route('/application',methods = ['GET', 'POST'])
def application():
    td = time.strftime('%Y-%m-%d', time.localtime())
    tt = time.strftime('%H:%M:%S', time.localtime())
    form = MyForm.MyFormApplication()
    db_publish_application = db_op.publish_application
    db_sql_execute = db_op.sql_execute
    db_work_order = db_op.work_order
    Msg = []
    source = 'ensure_application'
    try:
        if form.submit.data:
            db_sso = db_op.user_sso
            op_mails = db_sso.query.with_entities(db_sso.mail).filter(db_sso.grade.like('1%')).all()
            op_mails = [mails[0] for mails in op_mails]
            op_mails.append(server_auth_leader)
            work_number = int(time.time())
            project = form.project.data.strip()
            tag = form.tag.data.strip()
            leader = form.leader.data.strip()
            git_url = form.git_url.data.strip()
            describe = form.describe.data.strip()
            comment = form.comment.data.strip()
            sql_execute = form.sql_run.data
            test = form.test.data
            nottest = form.nottest.data
            product = form.product.data
            ticket = Md5.Md5_make('%s%s' %(project,tag))
            sql_html = '<br>'
            if not product and (not test or not nottest):
                raise Msg.extend(('error', '产品、测试确认项没有选择!'))
            if project.endswith('.git'):
                raise Msg.extend(('error', '%s 项目名称并非GIT地址!' % project))
            if '@' not in leader:
                raise Msg.extend(('error', '%s 邮箱地址格式错误!' %leader))
            if not check_mail(leader):
                raise Msg.extend(('error', '%s 邮箱核实未通过,请确认邮箱正确以及审核人登录过该平台!' % leader))
            if leader == g.mail and tools.check_env() !='dev':
                raise Msg.extend(('error', '%s 邮箱地址与申请人邮箱重复!' % leader))
            if leader in op_mails:
                raise Msg.extend(('error', '%s 邮箱地址不能是运维人员邮箱!' % leader))
            val = db_publish_application.query.filter(and_(db_publish_application.project == project, db_publish_application.version == tag)).all()
            if val:
                raise Msg.extend(('error', '%s:%s申请工单已存在!' %(project,tag)))
            url = 'https://{0}/{1}?ticket={2}'.format(request.host, source, ticket)
            ensure_url = '<p>工单受理(运维专属链接):<a href="%s">%s</a></p>' % (url,url)
            msg_url = '工单受理:%s' % url
            review_url = '<p>审核地址:<a href="https://{0}/work_review">https://{0}/work_review</a></p>'.format(request.host)
            text = ['### 项目上线发布申请:',
                    "> **工单号:%s**" % work_number,
                    "> **项目名称:%s**" %project,
                    "> **Tag:%s**" %tag,
                    "> **Git地址:%s**" %git_url,
                    "> **变更描述:%s**" %describe
                    ]
            # 判断是否需要执行sql
            if sql_execute:
                sql_url = None
                sql_server = form.sql_server.data
                sql_port = form.sql_port.data
                sql_database = form.sql_database.data
                sql_md5 = form.sql_md5.data
                sql_describe = form.sql_describe.data
                # sql申请记录
                if sql_server and sql_port and sql_database and request.files['sql_file'] and sql_md5 and sql_describe:
                    File = request.files['sql_file']
                    if File.filename.endswith('.zip'):
                        try:
                            file_dir = '/tmp/op_download'
                            if not os.path.exists(file_dir):
                                os.system("mkdir -p %s" %file_dir)
                            file_path = "%s/%s" % (file_dir,File.filename)
                            if os.path.exists(file_path):
                                os.remove(file_path)
                            File.save(file_path)
                            if os.path.exists(file_path):
                                #验证md5
                                if sql_md5 == Md5.Md5_file(file_path):
                                    #上传至oss
                                    try:
                                        auth = oss2.Auth(oss_id, oss_key)
                                        bucket = oss2.Bucket(auth, oss_url, 'ops')
                                        bucket.put_object_from_file('op_download/%s' %File.filename,file_path)
                                        sql_url = 'https://ops.oss-cn-beijing.aliyuncs.com/op_download/{}'.format(File.filename)
                                    except:
                                        raise Msg.extend(('error', '文件上传oss失败!'))
                                else:
                                    raise Msg.extend(('error', '文件md5验证错误!'))
                            else:
                                raise Msg.extend(('error', '文件上传失败!'))
                        except Exception as e:
                            logging.error(e)
                    else:
                        raise Msg.extend(('error', 'sql文件类型不符合要求!'))
                    if sql_url:
                        text.extend(['### 需要执行sql语句:',
                                     '> **服务器:%s**' % sql_server,
                                     '> **端口:%s**' % sql_port,
                                     '> **数据库:%s**' % sql_database,
                                     '> **文件地址:%s**' % sql_url,
                                     '> **变更说明:%s**' % sql_describe])
                        sql_html = '<p style="color:red">需执行以下SQL:</p><table style="border:1px solid black;border-collapse:collapse;width:1200;"> \
                                    <tr> \
                                       <th style="border:1px solid black;text-align:center;vertical-align:middle;">服务器</th> \
                                       <th style="border:1px solid black;text-align:center;vertical-align:middle;">端口</th> \
                                       <th style="border:1px solid black;text-align:center;vertical-align:middle;">数据库</th> \
                                       <th style="border:1px solid black;text-align:center;vertical-align:middle;">文件地址</th> \
                                       <th style="border:1px solid black;text-align:center;vertical-align:middle;">变更说明</th> \
                                       <th style="border:1px solid black;text-align:center;vertical-align:middle;">申请人</th> \
                                    </tr> \
                                    <tr> \
                                       <td style="border:1px solid black;text-align:center;vertical-align:middle;">%s</td> \
                                       <td style="border:1px solid black;text-align:center;vertical-align:middle;">%s</td> \
                                        <td style="border:1px solid black;text-align:center;vertical-align:middle;">%s</td> \
                                        <td style="border:1px solid black;text-align:center;vertical-align:middle;">%s</td> \
                                        <td style="border:1px solid black;text-align:center;vertical-align:middle;"> \
                                            <div style="width:300;word-wrap: break-word;word-break: break-all;overflow: hidden;">%s</div></td> \
                                        <td style="border:1px solid black;text-align:center;vertical-align:middle;">%s</td> \
                                    </tr></table><br>' % (sql_server, sql_port, sql_database, sql_url, sql_describe, g.user)
                    else:
                        raise Msg.extend(('error', '文件上传失败!'))
                else:
                    raise Msg.extend(('error', 'sql执行信息均为必填项!'))
            text.append("##### 申请人:%s" %g.user)
            mail_html = '<p>工单号:%s</p><table style="border:1px solid black;border-collapse:collapse;width:1200;"> \
                        <tr> \
                           <th style="border:1px solid black;text-align:center;vertical-align:middle;">项目名称</th> \
                           <th style="border:1px solid black;text-align:center;vertical-align:middle;">Tag</th> \
                           <th style="border:1px solid black;text-align:center;vertical-align:middle;">Git地址</th> \
                           <th style="border:1px solid black;text-align:center;vertical-align:middle;">变更描述</th> \
                           <th style="border:1px solid black;text-align:center;vertical-align:middle;">申请人</th> \
                        </tr> \
                        <tr> \
                           <td style="border:1px solid black;text-align:center;vertical-align:middle;">%s</td> \
                           <td style="border:1px solid black;text-align:center;vertical-align:middle;"> \
                                <div style="width:300;word-wrap: break-word;word-break: break-all;overflow: hidden;">%s</div></td> \
                           <td style="border:1px solid black;text-align:center;vertical-align:middle;"> \
                                <div style="width:300;word-wrap: break-word;word-break: break-all;overflow: hidden;">%s</div></td> \
                           <td style="border:1px solid black;text-align:center;vertical-align:middle;"> \
                                <div style="width:300;word-wrap: break-word;word-break: break-all;overflow: hidden;">%s</div></td> \
                           <td style="border:1px solid black;text-align:center;vertical-align:middle;">%s</td> \
                        </tr></table>' % (work_number,project, tag, git_url, describe, g.user)
            # 记录工单受理凭证
            Redis.set('work_order_ticket_%s' % ticket, work_number)
            # 记录发送钉钉内容
            Redis.set('op_send_dingding_msg_%s' % work_number,text)
            # 记录发送邮件内容
            Redis.set('op_send_mail_html_%s' % work_number, '%s%s' % (mail_html,sql_html))
            #记录受理工单链接
            Redis.set('op_send_ensure_url_%s' % work_number,ensure_url)
            Redis.set('op_send_msg_url_%s' % work_number, msg_url)
            try:
                if sql_execute:
                    c = db_sql_execute(date=td,time=tt,dingid=g.dingId, host=sql_server.strip(), port=sql_port.strip(),
                                       database=sql_database.strip(), sql_url=sql_url.strip(),
                                       sql_md5=sql_md5.strip(), describe=sql_describe.strip(),work_number=work_number)
                    db_op.DB.session.add(c)
                    db_op.DB.session.commit()
                # 项目申请记录
                c = db_publish_application(date=td,time=tt,dingid=g.dingId,
                                           project=project, version=tag, git_url=git_url,
                                           describe=describe,comment=comment,test=test,
                                           nottest=nottest,product=product, work_number=work_number)
                db_op.DB.session.add(c)
                db_op.DB.session.commit()
                # 记录任务流水状态
                c = db_work_order(date=td,work_number=work_number, source=source,applicant=g.dingId,reviewer=leader,
                                  approval='',dingid='',status='未审核')
                db_op.DB.session.add(c)
                db_op.DB.session.commit()
            except Exception as e:
                Msg.extend(('error', '%s上线申请提交失败!' % project))
                logging.error(e)
            else:
                try:
                    cc_mail = [g.mail]
                    if int(test) == 1:
                        cc_mail.append(test_mail)
                        # 记录是否抄送给测试
                        Redis.set('op_cc_test_mail_%s' % work_number, test_mail)
                    msg = Message("%s项目上线发布申请" % project, sender=sender, recipients=[leader, receiver],
                                  cc=cc_mail)
                    # 发送邮件
                    msg.html = '%s%s%s%s' % (mail_html, sql_html,'<p style="color:red">项目负责人审核后自动邮件通知</p>',review_url)
                    with app.app_context():
                        mail.send(msg)
                    # 发送钉钉
                    text.append('##### 项目负责人审核后自动消息通知')
                    tools.dingding_msg(text, token=work_token)
                except Exception as e:
                    logging.error(e)
                    Msg.extend(('error', '%s上线申请通知失败!' % project))
                else:
                    Msg.extend(('success', '%s上线申请通知完成!' % project))
    except Exception as e:
        Msg.extend(('error', '内部未知错误!'))
        logging.error(e)
    finally:
        db_project = db_op.project_list
        projects = db_project.query.with_entities(distinct(db_project.project)).all()
        projects = [project[0] for project in projects]
        projects = json.dumps([{"id": str(i), "text": str(project)} for i, project in enumerate(projects)])
    return render_template('application.html',form=form,Msg=Msg,projects=projects)

@page_work_order.route('/server_auth',methods = ['GET', 'POST'])
def server_auth():
    td = time.strftime('%Y-%m-%d', time.localtime())
    tt = time.strftime('%H:%M:%S', time.localtime())
    form = MyForm.MyFormServerAuth()
    db_server_auth = db_op.server_auth
    db_work_order = db_op.work_order
    Msg = []
    source = 'ensure_server_auth'
    try:
        if form.submit.data:
            db_sso = db_op.user_sso
            op_mails = db_sso.query.with_entities(db_sso.mail).filter(db_sso.grade.like('1%')).all()
            op_mails = [mails[0] for mails in op_mails]
            op_mails.append(server_auth_leader)
            work_number = int(time.time())
            leader = form.leader.data.strip()
            servers = form.servers.data.strip()
            auth_level = form.auth_level.data.strip()
            purpose = form.purpose.data.strip()
            ticket = Md5.Md5_make('%s%s' % (servers,auth_level))
            url = 'https://{0}/{1}?ticket={2}'.format(request.host, source, ticket)
            ensure_url = '<p>工单受理(运维专属链接):<a href="%s">%s</a></p>' % (url,url)
            msg_url = '工单受理:%s' % url
            review_url = '<p>审核地址:<a href="https://{0}/work_review">https://{0}/work_review</a></p>'.format(request.host)
            if '@' not in leader:
                raise Msg.extend(('error', '%s 邮箱地址格式错误!' %leader))
            if not check_mail(leader):
                raise Msg.extend(('error', '%s 邮箱核实未通过,请确认邮箱正确以及审核人登录过该平台!' % leader))
            if leader == g.mail and tools.check_env() !='dev':
                raise Msg.extend(('error', '%s 邮箱地址与申请人邮箱重复!' % leader))
            if leader in op_mails:
                raise Msg.extend(('error', '%s 邮箱地址不能是运维人员邮箱!' % leader))
            val = db_server_auth.query.filter(
                and_(db_server_auth.servers == servers, db_server_auth.auth_level == auth_level)).all()
            if val:
                raise Msg.extend(('error', '申请工单已存在!'))
            text = ['### 服务器权限申请:',
                    "> **工单号:%s**" % work_number,
                    "> **服务器:%s**" %servers,
                    "> **申请权限:%s**" %auth_level,
                    "> **功能描述:%s**" %purpose
                    ]
            text.append("##### 申请人:%s" %g.user)
            mail_html = '<p>工单号:%s</p><table style="border:1px solid black;border-collapse:collapse;width:1200;"> \
                            <tr> \
                               <th style="border:1px solid black;text-align:center;vertical-align:middle;">服务器列表</th> \
                               <th style="border:1px solid black;text-align:center;vertical-align:middle;">申请权限</th> \
                               <th style="border:1px solid black;text-align:center;vertical-align:middle;">功能描述</th> \
                               <th style="border:1px solid black;text-align:center;vertical-align:middle;">申请人</th> \
                            </tr> \
                            <tr> \
                               <td style="border:1px solid black;text-align:center;vertical-align:middle;"> \
                                    <div style="width:300;word-wrap: break-word;word-break: break-all;overflow: hidden;">%s</div></td> \
                               <td style="border:1px solid black;text-align:center;vertical-align:middle;"> \
                                    <div style="width:300;word-wrap: break-word;word-break: break-all;overflow: hidden;">%s</div></td> \
                               <td style="border:1px solid black;text-align:center;vertical-align:middle;"> \
                                    <div style="width:300;word-wrap: break-word;word-break: break-all;overflow: hidden;">%s</div></td> \
                               <td style="border:1px solid black;text-align:center;vertical-align:middle;">%s</td> \
                            </tr></table><br>' %(work_number,servers,auth_level,purpose,g.user)
            #记录工单受理地址
            Redis.set('op_send_mail_url_%s' % work_number, ensure_url)
            Redis.set('op_send_dingding_url_%s' % work_number,msg_url)
            # 记录工单受理凭证
            Redis.set('work_order_ticket_%s' % ticket, work_number)
            # 记录发送邮件内容
            Redis.set('op_send_mail_html_%s' % work_number, mail_html)
            # 记录发送钉钉消息
            Redis.set('op_send_dingding_msg_%s' % work_number, text)
            try:
                # 项目申请记录
                c = db_server_auth(date=td,time=tt,dingid=g.dingId,
                                           leader=leader, servers=servers,
                                           auth_level=auth_level,
                                           purpose=purpose, work_number = work_number)
                db_op.DB.session.add(c)
                db_op.DB.session.commit()
                # 记录任务流水状态
                c = db_work_order(date=td,work_number = work_number, source=source,applicant=g.dingId,reviewer=leader,
                                  approval=server_auth_leader,dingid='',status='未审核')
                db_op.DB.session.add(c)
                db_op.DB.session.commit()
            except Exception as e:
                Msg.extend(('error', '服务器权限申请提交失败!'))
                logging.error(e)
            else:
                try:
                    msg = Message("服务器权限申请", sender=sender, recipients=[leader, receiver], cc=[g.mail],charset='utf-8')
                    msg.html = '%s%s%s' %(mail_html,review_url,'<p style="color:red">审核人审核后自动邮件通知</p>')
                    with app.app_context():
                        mail.send(msg)
                    # 发送钉钉
                    text.append('##### 审核人审核后自动邮件通知')
                    tools.dingding_msg(text, token=work_token)
                except Exception as e:
                    logging.error(e)
                    Msg.extend(('error', '服务器权限申请失败!'))
                else:
                    Msg.extend(('success','服务器权限申请完成!'))
    except Exception as e:
        Msg.extend(('error', '内部未知错误!'))
        logging.error(e)
    return render_template('server_auth.html', form=form, Msg=Msg)

@page_work_order.route('/sql_execute',methods = ['GET', 'POST'])
def sql_execute():
    td = time.strftime('%Y-%m-%d', time.localtime())
    tt = time.strftime('%H:%M:%S', time.localtime())
    form = MyForm.MyFormSqlExecute()
    db_sql_execute = db_op.sql_execute
    db_work_order = db_op.work_order
    Msg = []
    source = 'ensure_sql_execute'
    try:
        if form.submit.data:
            db_sso = db_op.user_sso
            op_mails = db_sso.query.with_entities(db_sso.mail).filter(db_sso.grade.like('1%')).all()
            op_mails = [mails[0] for mails in op_mails]
            op_mails.append(server_auth_leader)
            work_number = int(time.time())
            sql_server = form.sql_server.data.strip()
            sql_port = form.sql_port.data.strip()
            sql_database = form.sql_database.data.strip()
            sql_md5 = form.sql_md5.data.strip()
            sql_describe = form.sql_describe.data.strip()
            leader = form.leader.data.strip()
            ticket = Md5.Md5_make(sql_md5)
            if '@' not in leader:
                raise Msg.extend(('error', '%s 邮箱地址格式错误!' %leader))
            if not check_mail(leader):
                raise Msg.extend(('error', '%s 邮箱核实未通过,请确认邮箱正确以及审核人登录过该平台!' % leader))
            if leader == g.mail and tools.check_env() !='dev':
                raise Msg.extend(('error', '%s 邮箱地址与申请人邮箱重复!' % leader))
            if leader in op_mails:
                raise Msg.extend(('error', '%s 邮箱地址不能是运维人员邮箱!' % leader))
            val = db_sql_execute.query.filter(db_sql_execute.sql_md5 == sql_md5).all()
            if val:
                raise Msg.extend(('error', '提交的内容工单已存在!'))
            review_url = '<p>审核地址:<a href="https://{0}/work_review">https://{0}/work_review</a></p>'.format(request.host)
            url = 'https://{0}/{1}?ticket={2}'.format(request.host, source, ticket)
            ensure_url = '<p>工单受理(运维专属链接):<a href="%s">%s</a></p>' % (url,url)
            msg_url = '工单受理:%s' % url
            sql_url = None
            if sql_server and sql_port and sql_database and request.files['sql_file'] and sql_md5 and sql_describe:
                File = request.files['sql_file']
                if File.filename.endswith('.zip'):
                    try:
                        file_dir = '/tmp/op_download'
                        if not os.path.exists(file_dir):
                            os.system("mkdir -p %s" %file_dir)
                        file_path = "%s/%s" % (file_dir,File.filename)
                        if os.path.exists(file_path):
                            os.remove(file_path)
                        File.save(file_path)
                        if os.path.exists(file_path):
                            #验证文件md5
                            if sql_md5 == Md5.Md5_file(file_path):
                                try:
                                    #上传至oss
                                    auth = oss2.Auth(oss_id, oss_key)
                                    bucket = oss2.Bucket(auth, oss_url, 'ops')
                                    bucket.put_object_from_file('op_download/%s' %File.filename,file_path)
                                    sql_url = 'https://ops.oss-cn-beijing.aliyuncs.com/op_download/{}'.format(File.filename)
                                except:
                                    raise Msg.extend(('error', '文件上传oss失败!'))
                            else:
                                raise Msg.extend(('error', '文件md5验证错误!'))
                        else:
                            raise Msg.extend(('error', '文件上传失败!'))
                    except Exception as e:
                        logging.error(e)
                else:
                    raise Msg.extend(('error', 'sql文件类型不符合要求!'))
            else:
                raise Msg.extend(('error', 'sql执行信息不完整!'))
            if sql_url:
                text = ['### 线上SQL执行申请:',
                        '> **工单号:%s**' % work_number,
                         '> **服务器:%s**' % sql_server,
                         '> **端口:%s**' % sql_port,
                         '> **数据库:%s**' % sql_database,
                         '> **文件地址:%s**' % sql_url,
                         '> **变更说明:%s**' % sql_describe]
                mail_html = '<p>工单号:%s</p><table style="border:1px solid black;border-collapse:collapse;width:1200;"> \
                            <tr> \
                               <th style="border:1px solid black;text-align:center;vertical-align:middle;">服务器</th> \
                               <th style="border:1px solid black;text-align:center;vertical-align:middle;">端口</th> \
                               <th style="border:1px solid black;text-align:center;vertical-align:middle;">数据库</th> \
                               <th style="border:1px solid black;text-align:center;vertical-align:middle;">文件地址</th> \
                                <th style="border:1px solid black;text-align:center;vertical-align:middle;">变更说明</th> \
                               <th style="border:1px solid black;text-align:center;vertical-align:middle;">申请人</th> \
                            </tr> \
                            <tr> \
                               <td style="border:1px solid black;text-align:center;vertical-align:middle;">%s</td> \
                               <td style="border:1px solid black;text-align:center;vertical-align:middle;">%s</td> \
                                <td style="border:1px solid black;text-align:center;vertical-align:middle;">%s</td> \
                                <td style="border:1px solid black;text-align:center;vertical-align:middle;">%s</td> \
                                <td style="border:1px solid black;text-align:center;vertical-align:middle;"> \
                                    <div style="width:300;word-wrap: break-word;word-break: break-all;overflow: hidden;">%s</div></td> \
                                <td style="border:1px solid black;text-align:center;vertical-align:middle;">%s</td> \
                            </tr></table><br>' % (work_number,sql_server, sql_port, sql_database, sql_url,sql_describe, g.user)
            else:
                raise Msg.extend(('error', 'sql文件上传失败!'))
            text.append("##### 申请人:%s" %g.user)
            # 记录工单受理凭证
            Redis.set('work_order_ticket_%s' % ticket, work_number)
            # 记录发送邮件内容
            Redis.set('op_send_mail_html_%s' % work_number, mail_html)
            # 记录发送钉钉内容
            Redis.set('op_send_dingding_msg_%s' % work_number, text)
            # 记录受理工单链接
            Redis.set('op_send_ensure_url_%s' % work_number, ensure_url)
            Redis.set('op_send_msg_url_%s' % work_number, msg_url)
            try:
                c = db_sql_execute(date=td,time=tt,dingid=g.dingId, host=sql_server, port=sql_port,
                                   database=sql_database, sql_url=sql_url,
                                   sql_md5=sql_md5, describe=sql_describe,work_number=work_number)
                db_op.DB.session.add(c)
                db_op.DB.session.commit()
                # 记录任务流水状态
                c = db_work_order(date=td,work_number=work_number, source=source,applicant=g.dingId,reviewer=leader,
                                  approval='',dingid='',status='未审核')
                db_op.DB.session.add(c)
                db_op.DB.session.commit()
            except Exception as e:
                Msg.extend(('error', '申请提交失败!'))
                logging.error(e)
            else:
                try:
                    msg = Message("线上SQL执行申请", sender=sender, recipients=[leader, receiver],cc=[g.mail],charset='utf-8')
                    msg.html = '%s%s%s' % (mail_html, '<p style="color:red">审核人审核后自动邮件通知</p>', review_url)
                    with app.app_context():
                        mail.send(msg)
                    # 发送钉钉
                    text.append('##### 审核人审核后自动消息通知')
                    tools.dingding_msg(text, token=work_token)
                except Exception as e:
                    logging.error(e)
                    Msg.extend(('error', '申请通知失败!'))
                else:
                    Msg.extend(('success', '申请通知完成!'))
    except Exception as e:
        Msg.extend(('error', '内部未知错误!'))
        logging.error(e)
    return render_template('sql_execute.html',form=form,Msg=Msg)

@page_work_order.route('/project_offline',methods = ['GET', 'POST'])
def project_offline():
    td = time.strftime('%Y-%m-%d', time.localtime())
    tt = time.strftime('%H:%M:%S', time.localtime())
    form = MyForm.MyFormProjectOffline()
    db_project_offline = db_op.project_offline
    db_work_order = db_op.work_order
    Msg = []
    source = 'ensure_project_offline'
    try:
        if form.submit.data:
            db_sso = db_op.user_sso
            op_mails = db_sso.query.with_entities(db_sso.mail).filter(db_sso.grade.like('1%')).all()
            op_mails = [mails[0] for mails in op_mails]
            op_mails.append(server_auth_leader)
            work_number = int(time.time())
            project = form.project.data.strip()
            leader = form.leader.data.strip()
            git_url = form.git_url.data.strip()
            describe = form.describe.data.strip()
            comment = form.comment.data.strip()
            ticket = Md5.Md5_make(project)
            if project.endswith('.git'):
                raise Msg.extend(('error', '%s 项目名称并非GIT地址!' % project))
            if '@' not in leader:
                raise Msg.extend(('error', '%s 邮箱地址格式错误!' %leader))
            if not check_mail(leader):
                raise Msg.extend(('error', '%s 邮箱核实未通过,请确认邮箱正确以及审核人登录过该平台!' % leader))
            if leader == g.mail and tools.check_env() !='dev':
                raise Msg.extend(('error', '%s 邮箱地址与申请人邮箱重复!' % leader))
            if leader in op_mails:
                raise Msg.extend(('error', '%s 邮箱地址不能是运维人员邮箱!' % leader))
            val = db_project_offline.query.filter(db_project_offline.project == project).all()
            if val:
                raise Msg.extend(('error', '%s申请工单已存在!' %project))
            review_url = '<p>审核地址:<a href="https://{0}/work_review">https://{0}/work_review</a></p>'.format(request.host)
            url = 'https://{0}/{1}?ticket={2}'.format(request.host,source,ticket)
            ensure_url = '<p>工单受理(运维专属链接):<a href="%s">%s</a></p>' %(url,url)
            msg_url = '工单受理:%s' %url
            text = ['### 线上项目下线申请:',
                    "> **工单号:%s**" % work_number,
                    "> **项目名称:%s**" %project,
                    "> **Git地址:%s**" %git_url,
                    "> **变更描述:%s**" %describe
                    ]
            text.append("##### 申请人:%s" %g.user)
            mail_html = '<p>工单号:%s</p><table style="border:1px solid black;border-collapse:collapse;width:1200;"> \
                        <tr> \
                           <th style="border:1px solid black;text-align:center;vertical-align:middle;">项目名称</th> \
                           <th style="border:1px solid black;text-align:center;vertical-align:middle;">Git地址</th> \
                           <th style="border:1px solid black;text-align:center;vertical-align:middle;">变更描述</th> \
                           <th style="border:1px solid black;text-align:center;vertical-align:middle;">申请人</th> \
                        </tr> \
                        <tr> \
                           <td style="border:1px solid black;text-align:center;vertical-align:middle;">%s</td> \
                      <td style="border:1px solid black;text-align:center;vertical-align:middle;"> \
                        <div style="width:300;word-wrap: break-word;word-break: break-all;overflow: hidden;">%s</div></td> \
                      <td style="border:1px solid black;text-align:center;vertical-align:middle;"> \
                        <div style="width:300;word-wrap: break-word;word-break: break-all;overflow: hidden;">%s</div></td> \
                      <td style="border:1px solid black;text-align:center;vertical-align:middle;">%s</td> \
                        </tr></table>' % (work_number,project, git_url, describe, g.user)
            # 记录工单受理凭证
            Redis.set('work_order_ticket_%s' % ticket, work_number)
            # 记录发送邮件内容
            Redis.set('op_send_mail_html_%s' % work_number, mail_html)
            # 记录发送钉钉内容
            Redis.set('op_send_dingding_msg_%s' % work_number, text)
            # 记录受理工单链接
            Redis.set('op_send_ensure_url_%s' % work_number, ensure_url)
            Redis.set('op_send_msg_url_%s' % work_number, msg_url)
            try:
                # 项目申请记录
                c = db_project_offline(date=td,time=tt,dingid=g.dingId,
                                           project=project, git_url=git_url,
                                           describe=describe,comment=comment, work_number=work_number)
                db_op.DB.session.add(c)
                db_op.DB.session.commit()
                # 记录任务流水状态
                c = db_work_order(date=td,work_number=work_number, source=source, applicant=g.dingId,reviewer=leader,
                                  approval='',dingid='',status='未审核')
                db_op.DB.session.add(c)
                db_op.DB.session.commit()
            except Exception as e:
                Msg.extend(('error', '%s下线申请提交失败!' % project))
                logging.error(e)
            else:
                try:
                    msg = Message("%s线上项目下线申请" % project, sender=sender, recipients=[leader, receiver],
                                  cc=[g.mail],charset='utf-8')
                    msg.html = '%s%s%s' % (mail_html, '<p style="color:red">审核人审核后自动邮件通知</p>', review_url)
                    with app.app_context():
                        mail.send(msg)
                    # 发送钉钉
                    text.append('##### 审核人审核后自动消息通知')
                    tools.dingding_msg(text, token=work_token)
                except Exception as e:
                    logging.error(e)
                    Msg.extend(('error', '%s下线申请通知失败!' % project))
                else:
                    Msg.extend(('success', '%s下线申请通知完成!' % project))
    except Exception as e:
        Msg.extend(('error', '内部未知错误!'))
        logging.error(e)
    finally:
        db_project = db_op.project_list
        projects = db_project.query.with_entities(distinct(db_project.project)).all()
        projects = [project[0] for project in projects]
        projects = json.dumps([{"id": str(i), "text": str(project)} for i, project in enumerate(projects)])
    return render_template('project_offline.html',form=form,Msg=Msg,projects=projects)

@page_work_order.route('/other_work',methods = ['GET', 'POST'])
def other_work():
    td = time.strftime('%Y-%m-%d', time.localtime())
    tt = time.strftime('%H:%M:%S', time.localtime())
    form = MyForm.MyFormOtherWork()
    db_other_work = db_op.other_work
    db_work_order = db_op.work_order
    db_sso = db_op.user_sso
    Msg = []
    source = 'ensure_other_work'
    receiver = app.config.get('DEFAULT_RECEIVER')
    review_url = '<p>审核地址:<a href="https://{0}/work_review">https://{0}/work_review</a></p>'.format(request.host)
    try:
        if form.submit.data:
            op_mails = db_sso.query.with_entities(db_sso.mail).filter(db_sso.grade.like('1%')).all()
            op_mails = [mails[0] for mails in op_mails]
            op_mails.append(server_auth_leader)
            work_number = int(time.time())
            describe = form.text.data
            title = form.titles.data
            leader = form.input.data
            assign = form.assign.data
            if assign != 'default':
                receiver = assign
            if '@' not in leader:
                raise Msg.extend(('error', '邮箱格式错误!'))
            if not check_mail(leader):
                raise Msg.extend(('error', '%s 邮箱核实未通过,请确认邮箱正确以及审核人登录过该平台!' % leader))
            if leader == g.mail and tools.check_env() !='dev':
                raise Msg.extend(('error', '%s 邮箱地址与申请人邮箱重复!' % leader))
            if leader in op_mails:
                raise Msg.extend(('error', '%s 邮箱地址不能是运维人员邮箱!' % leader))
            if not describe:
                raise Msg.extend(('error', '工单内容不能为空!'))
            md5 = Md5.Md5_make(describe)
            ticket = Md5.Md5_make(md5)
            val = db_other_work.query.filter(db_other_work.md5 == md5).all()
            if val:
                raise Msg.extend(('error', '提交的内容工单已存在!'))
            try:
                url = 'https://{0}/{1}?ticket={2}'.format(request.host, source, ticket)
                ensure_url = '<p>工单受理(运维专属链接):<a href="%s">%s</a></p>' %(url,url)
                msg_url = '工单受理:%s' % url
                text = ['### 运维其它事项申请工单:',
                        '> **工单号:%s**' % work_number,
                        '> **事项标题:%s**' % title,
                         '> **事项描述:%s**' % describe]
                mail_html = '<p>工单号:%s</p> \
                            <p>事项标题:%s</p> \
                            <div><p>事项描述:</p>%s</div> \
                            <p>申请人:%s</p><br>' % (work_number,title, describe, g.user)
            except Exception as e:
                logging.error(e)
            else:
                text.append("##### 申请人:%s" %g.user)
                # 记录工单受理凭证
                Redis.set('work_order_ticket_%s' % ticket, work_number)
                # 记录发送邮件内容
                Redis.set('op_send_mail_html_%s' % work_number, mail_html)
                # 记录发送钉钉内容
                Redis.set('op_send_dingding_msg_%s' % work_number, text)
                # 记录受理工单链接
                Redis.set('op_send_ensure_url_%s' % work_number, ensure_url)
                Redis.set('op_send_msg_url_%s' % work_number, msg_url)
                try:
                    describe = ''.join([line for line in describe.splitlines() if line])
                    c = db_other_work(date=td,time=tt,dingid=g.dingId,title=title,describe=describe,md5=md5,work_number=work_number)
                    db_op.DB.session.add(c)
                    db_op.DB.session.commit()
                    # 记录任务流水状态
                    dingid = ''
                    if assign != 'default':
                        dingid = db_sso.query.with_entities(db_sso.dingunionid).filter(db_sso.mail==receiver).all()
                        dingid = dingid[0][0]
                    c = db_work_order(date=td,work_number=work_number, source=source,applicant=g.dingId,reviewer =leader,
                                      approval='',dingid=dingid,status='未审核')
                    if 'VPN' in title or '后台' in title:
                        c = db_work_order(date=td, work_number=work_number, source=source, applicant=g.dingId,
                                          reviewer='',approval=server_auth_leader,dingid=dingid,status='待审批')
                    db_op.DB.session.add(c)
                    db_op.DB.session.commit()
                except Exception as e:
                    Msg.extend(('error', '申请提交失败!'))
                    logging.error(e)
                else:
                    try:
                        #记录受理人邮箱
                        Redis.set('op_other_work_receiver_%s' % work_number, receiver)
                        msg = Message("运维其它事项申请工单", sender=sender, recipients=[receiver,leader],
                                      cc=[g.mail],charset='utf-8')
                        msg.html = '%s%s%s' % (mail_html, '<p style="color:red">审核人审核后自动邮件通知</p>', review_url)
                        if 'VPN' in title or '后台' in title:
                            msg = Message("运维其它事项申请工单", sender=sender, recipients=[receiver],
                                          cc=[g.mail], charset='utf-8')
                            msg.html = '%s%s' % (mail_html, '<p style="color:red">审核人审批后自动邮件通知</p>')
                        with app.app_context():
                            mail.send(msg)
                        # 发送钉钉
                        text.append('##### 审核人审核后自动消息通知')
                        tools.dingding_msg(text, token=work_token)
                    except Exception as e:
                        logging.error(e)
                        Msg.extend(('error', '申请通知失败!'))
                    else:
                        Msg.extend(('success', '申请通知完成!'))
    except Exception as e:
        Msg.extend(('error', '内部未知错误!'))
        logging.error(e)
    return render_template('other_work.html',form=form,Msg=Msg,server_auth_leader=server_auth_leader)

@page_work_order.route('/work_details/application/<work_number>')
def work_application_details(work_number=None):
    publish_info = defaultdict()
    try:
        db_sso = db_op.user_sso
        db_publish_application = db_op.publish_application
        db_sql_execute = db_op.sql_execute
        if work_number:
            infos = db_sso.query.with_entities(db_sso.dingunionid, db_sso.realName, db_sso.department).all()
            users = {info[0]: info[1:] for info in infos}
            task_records = db_publish_application.query.with_entities(db_publish_application.date,
                                                                      db_publish_application.time,
                                                                      db_publish_application.project,
                                                                      db_publish_application.version,
                                                                      db_publish_application.git_url,
                                                                      db_publish_application.describe,
                                                                      db_publish_application.comment,
                                                                      db_publish_application.dingid).filter(
                db_publish_application.work_number==int(work_number)).all()
            if task_records:
                publish_info['task_records'] = task_records[0][:-1]
                sql_execute= db_sql_execute.query.with_entities(db_sql_execute.host,
                                                                 db_sql_execute.port,
                                                                 db_sql_execute.database,
                                                                 db_sql_execute.sql_url,
                                                                 db_sql_execute.sql_md5,
                                                                 db_sql_execute.describe).filter(
                    db_sql_execute.work_number==int(work_number)).all()
                if sql_execute:
                    publish_info['sql_execute'] = sql_execute[0]
                publish_info['reviewer'] = None
                publish_info['user_info'] = users[task_records[0][-1]]
    except Exception as e:
        logging.error(e)
    return render_template('work_application_details.html', publish_info = publish_info)

@page_work_order.route('/work_details/server_auth/<work_number>')
def server_auth_details(work_number=None):
    publish_info = defaultdict()
    try:
        db_sso = db_op.user_sso
        db_server_auth = db_op.server_auth
        if work_number:
            users = db_sso.query.with_entities(db_sso.dingunionid, db_sso.realName, db_sso.department).all()
            users = {info[0]: info[1:] for info in users}
            task_records = db_server_auth.query.with_entities(db_server_auth.date,
                                                              db_server_auth.time,
                                                              db_server_auth.servers,
                                                              db_server_auth.auth_level,
                                                              db_server_auth.purpose,
                                                              db_server_auth.dingid).filter(db_server_auth.work_number==int(work_number)).all()
            if task_records:
                publish_info['task_records'] = task_records[0][:-1]
                publish_info['user_info'] = users[task_records[0][-1]]
    except Exception as e:
        logging.error(e)
    return render_template('server_auth_details.html', publish_info=publish_info)

@page_work_order.route('/work_details/sql_execute/<work_number>')
def work_sql_execute_details(work_number=None):
    publish_info = defaultdict()
    try:
        db_sso = db_op.user_sso
        db_sql_execute = db_op.sql_execute
        if work_number:
            infos = db_sso.query.with_entities(db_sso.dingunionid, db_sso.realName, db_sso.department).all()
            users = {info[0]: info[1:] for info in infos}
            sql_execute= db_sql_execute.query.with_entities(db_sql_execute.date,
                                                            db_sql_execute.time,
                                                            db_sql_execute.host,
                                                             db_sql_execute.port,
                                                             db_sql_execute.database,
                                                             db_sql_execute.sql_url,
                                                             db_sql_execute.sql_md5,
                                                             db_sql_execute.describe,
                                                            db_sql_execute.dingid).filter(db_sql_execute.work_number==int(work_number)).all()
            if sql_execute:
                publish_info['sql_execute'] = sql_execute[0]
                publish_info['user_info'] = users[sql_execute[0][-1]]
    except Exception as e:
        logging.error(e)
    return render_template('sql_execute_details.html', publish_info=publish_info)

@page_work_order.route('/work_details/project_offline/<work_number>')
def project_offline_details(work_number=None):
    publish_info = defaultdict()
    try:
        db_sso = db_op.user_sso
        db_project_offline = db_op.project_offline
        if work_number:
            infos = db_sso.query.with_entities(db_sso.dingunionid, db_sso.realName,db_sso.department).all()
            users = {info[0]: info[1:] for info in infos}
            task_records = db_project_offline.query.with_entities(db_project_offline.date,
                                                                      db_project_offline.time,
                                                                      db_project_offline.project,
                                                                      db_project_offline.git_url,
                                                                      db_project_offline.describe,
                                                                      db_project_offline.comment,
                                                                      db_project_offline.dingid).filter(db_project_offline.work_number==int(work_number)).all()
            if task_records:
                publish_info['task_records'] = task_records[0][:-1]
                publish_info['user_info'] = users[task_records[0][-1]]
    except Exception as e:
        logging.error(e)
    return render_template('project_offline_details.html', publish_info=publish_info)

@page_work_order.route('/work_details/other_work/<work_number>')
def work_other_work_details(work_number=None):
    publish_info = defaultdict()
    try:
        db_sso = db_op.user_sso
        db_other_work = db_op.other_work
        if work_number:
            infos = db_sso.query.with_entities(db_sso.dingunionid, db_sso.realName,db_sso.department).all()
            users = {info[0]: info[1:] for info in infos}
            other_work= db_other_work.query.with_entities(db_other_work.date,
                                                           db_other_work.time,
                                                          db_other_work.title,
                                                           db_other_work.describe,
                                                           db_other_work.dingid).filter(db_other_work.work_number==int(work_number)).all()
            if other_work:
                publish_info['other_work'] = other_work[0]
                publish_info['user_info'] = users[other_work[0][-1]]
    except Exception as e:
        logging.error(e)
    return render_template('other_work_details.html', publish_info = publish_info)

@page_work_order.route('/work_order_list')
def work_order_list():
    tables = ('工单号', '日期', '项目名称', '版本', '描述', '申请人', '详情','问题备注', '工单状态')
    db_work_order = db_op.work_order
    db_publish_application = db_op.publish_application
    db_sql_execute = db_op.sql_execute
    db_sso = db_op.user_sso
    db_work_comment = db_op.work_comment
    action = tools.http_args(request, 'action')
    work_number = tools.http_args(request, 'work_number')
    Msg = None
    work_orders = []
    projects = defaultdict()
    source = 'ensure_application'
    try:

        if action and work_number:
            if action == 'repeal':
                try:
                    val = db_work_order.query.filter(and_(db_work_order.work_number==int(work_number),
                                                          db_work_order.source == source,
                                                          db_work_order.status.in_(('未审核','未受理','已退回','已拒绝')))).all()
                    if val:
                        try:
                            c = db_work_order.query.filter(db_work_order.work_number==int(work_number)).all()
                            for v in c:
                                db_op.DB.session.delete(v)
                                db_op.DB.session.commit()
                            c = db_publish_application.query.filter(db_publish_application.work_number==int(work_number)).all()
                            for v in c:
                                db_op.DB.session.delete(v)
                                db_op.DB.session.commit()
                            c = db_sql_execute.query.filter(db_sql_execute.work_number == int(work_number)).all()
                            for v in c:
                                db_op.DB.session.delete(v)
                                db_op.DB.session.commit()
                            # 清除工单问题备注表中相关工单信息
                            c = db_work_comment.query.filter(
                                db_work_comment.work_number == int(work_number)).all()
                            for v in c:
                                db_op.DB.session.delete(v)
                                db_op.DB.session.commit()
                        except Exception as e:
                            logging.error(e)
                            Msg = '%s工单撤销操作失败!' %work_number
                        else:
                            try:
                                # 钉钉通知工单撤销
                                text = ['### 工单撤销通知:',
                                        "> **工单号:%s**" % work_number,
                                        "> **撤销人:%s**" % g.user,
                                        ]
                                tools.dingding_msg(text, token=work_token)
                            except Exception as e:
                                logging.error(e)
                            # 消息通知工单撤销
                            Msg = '%s工单撤销成功!' % work_number
                            try:
                                # 邮件通知工单撤销
                                if Redis.exists('op_send_mail_html_%s' % work_number):
                                    mail_html = Redis.get('op_send_mail_html_%s' % work_number)
                                    cc_mail = [g.mail]
                                    if Redis.exists('op_cc_test_mail_%s' % work_number):
                                        cc_mail.append(Redis.get('op_cc_test_mail_%s' % work_number))
                                    msg = Message("%s工单撤销通知" % work_number, sender=sender, recipients=[receiver],
                                                  cc=cc_mail,charset='utf-8')
                                    alarm_html = '<p style="color:red">工单状态:申请人已主动撤销</p>'
                                    msg.html = '%s%s' %(mail_html, alarm_html)
                                    with app.app_context():
                                        mail.send(msg)
                            except Exception as e:
                                logging.error(e)
                    else:
                        Msg = '无效的工单撤销操作!'
                except Exception as e:
                    logging.error(e)
        users = db_sso.query.with_entities(db_sso.dingunionid, db_sso.realName).all()
        users = {info[0]: info[1:] for info in users}
        try:
            projects = db_publish_application.query.with_entities(db_publish_application.work_number,
                                                                  db_publish_application.date,
                                                                  db_publish_application.project,
                                                                  db_publish_application.version,
                                                                  db_publish_application.describe,
                                                                  db_publish_application.dingid).filter(db_publish_application.dingid==g.dingId).all()
            projects = {info[0]: info[1:] for info in projects}
            work_orders = db_work_order.query.with_entities(db_work_order.work_number,
                                                            db_work_order.dingid,
                                                            db_work_order.status).filter(and_(
                db_work_order.source == source, db_work_order.work_number.in_(projects.keys()))).order_by(
                desc(db_work_order.id)).all()
        except Exception as e:
            logging.error(e)
        if action and work_number:
            if action == 'query':
                work_orders = db_work_order.query.with_entities(db_work_order.work_number,
                                                                db_work_order.dingid,
                                                                db_work_order.status).filter(and_(
                    db_work_order.source == source, db_work_order.work_number == int(work_number))).all()
        if work_orders:
            work_orders = [list(info) for info in work_orders]
            for info in work_orders:
                info.extend(projects[info[0]][:-1])
                info.extend(users[projects[info[0]][-1]])
    except Exception as e:
        logging.error(e)
    return render_template('work_order_list.html', tables=tables, work_orders=work_orders,Msg=Msg,total='上线申请工单列表')

@page_work_order.route('/server_auth_list')
def server_auth_list():
    tables = ('工单号', '日期', '申请人', '部门', '服务器列表', '申请权限', '所属用途', '详情','问题备注', '工单状态')
    db_work_order = db_op.work_order
    db_server_auth = db_op.server_auth
    db_sso = db_op.user_sso
    db_work_comment = db_op.work_comment
    action = tools.http_args(request, 'action')
    work_number = tools.http_args(request, 'work_number')
    Msg = None
    work_orders = []
    source = 'ensure_server_auth'
    try:
        if action and work_number:
            if action == 'repeal':
                try:
                    val = db_work_order.query.filter(and_(db_work_order.work_number == int(work_number),
                                                          db_work_order.source==source,
                                                          db_work_order.status.in_(('未审核','待审批','未受理','已退回','审批拒绝')) )).all()
                    if val:
                        try:
                            c = db_work_order.query.filter(db_work_order.work_number==int(work_number)).all()
                            for v in c:
                                db_op.DB.session.delete(v)
                                db_op.DB.session.commit()
                            c = db_server_auth.query.filter(db_server_auth.work_number==int(work_number)).all()
                            for v in c:
                                db_op.DB.session.delete(v)
                                db_op.DB.session.commit()
                            # 清除工单问题备注表中相关工单信息
                            c = db_work_comment.query.filter(
                                db_work_comment.work_number == int(work_number)).all()
                            for v in c:
                                db_op.DB.session.delete(v)
                                db_op.DB.session.commit()
                        except Exception as e:
                            logging.error(e)
                            Msg = '%s工单撤销操作失败!' % work_number
                        else:
                            try:
                                # 钉钉通知工单撤销
                                text = ['### 工单撤销通知:',
                                        "> **工单号:%s**" % work_number,
                                        "> **撤销人:%s**" % g.user,
                                        ]
                                tools.dingding_msg(text, token=work_token)
                            except Exception as e:
                                logging.error(e)
                            # 消息通知工单撤销
                            Msg = '%s工单撤销成功!' % work_number
                            try:
                                # 邮件通知工单撤销
                                if Redis.exists('op_send_mail_html_%s' % work_number):
                                    mail_html = Redis.get('op_send_mail_html_%s' % work_number)
                                    alarm_html = '<p style="color:red">工单状态:申请人已主动撤销</p>'
                                    msg = Message("%s工单撤销通知" % work_number, sender=sender, recipients=[receiver],
                                                  cc=[g.mail],charset='utf-8')
                                    msg.html = '%s%s' % (mail_html, alarm_html)
                                    with app.app_context():
                                        mail.send(msg)
                            except Exception as e:
                                logging.error(e)

                    else:
                        Msg = '无效的工单撤销操作!'
                except Exception as e:
                    logging.error(e)
        users = db_sso.query.with_entities(db_sso.dingunionid, db_sso.realName, db_sso.department).all()
        users = {info[0]: info[1:] for info in users}
        servers = db_server_auth.query.with_entities(db_server_auth.work_number,
                                                             db_server_auth.date,
                                                             db_server_auth.servers,
                                                             db_server_auth.auth_level,
                                                             db_server_auth.purpose,
                                                             db_server_auth.dingid).filter(db_server_auth.dingid==g.dingId).all()
        servers = {info[0]: info[1:] for info in servers}
        work_orders = db_work_order.query.with_entities(db_work_order.work_number,
                                                        db_work_order.dingid,
                                                        db_work_order.status).filter(and_(
            db_work_order.source == source,db_work_order.work_number.in_(servers.keys()))).order_by(desc(db_work_order.id)).all()
        if action and work_number:
            if action == 'query':
                work_orders = db_work_order.query.with_entities(db_work_order.work_number,
                                                                db_work_order.dingid,
                                                                db_work_order.status).filter(and_(
                    db_work_order.source == source, db_work_order.work_number == int(work_number))).all()
        if work_orders:
            work_orders = [list(info) for info in work_orders]
            for info in work_orders:
                info.extend(servers[info[0]][:-1])
                info.insert(4, users[servers[info[0]][-1]][0])
                info.insert(5, users[servers[info[0]][-1]][-1])
    except Exception as e:
        logging.error(e)
    return render_template('server_auth_list.html', tables=tables, work_orders=work_orders,Msg=Msg,total='服务器权限工单列表')

@page_work_order.route('/sql_execute_list')
def sql_execute_list():
    tables = ('工单号', '日期', '服务器', '端口', '数据库', '变更描述', '申请人', '详情','问题备注', '工单状态')
    db_work_order = db_op.work_order
    db_sql_execute = db_op.sql_execute
    db_publish_application = db_op.publish_application
    db_sso = db_op.user_sso
    db_work_comment = db_op.work_comment
    action = tools.http_args(request, 'action')
    work_number = tools.http_args(request, 'work_number')
    Msg = None
    work_orders = []
    source = 'ensure_sql_execute'
    try:
        if action and work_number:
            if action == 'repeal':
                try:
                    val = db_work_order.query.filter(and_(db_work_order.work_number==int(work_number),
                                                          db_work_order.source==source,
                                                          db_work_order.status.in_(('未审核','未受理','已退回','已拒绝')))).all()
                    if val:
                        try:
                            c = db_work_order.query.filter(db_work_order.work_number == int(work_number)).all()
                            for v in c:
                                db_op.DB.session.delete(v)
                                db_op.DB.session.commit()
                            c = db_sql_execute.query.filter(db_sql_execute.work_number == int(work_number)).all()
                            for v in c:
                                db_op.DB.session.delete(v)
                                db_op.DB.session.commit()
                            c = db_publish_application.query.filter(
                                db_publish_application.work_number == int(work_number)).all()
                            if c:
                                for v in c:
                                    db_op.DB.session.delete(v)
                                    db_op.DB.session.commit()
                            # 清除工单问题备注表中相关工单信息
                            c = db_work_comment.query.filter(
                                db_work_comment.work_number == int(work_number)).all()
                            for v in c:
                                db_op.DB.session.delete(v)
                                db_op.DB.session.commit()
                        except Exception as e:
                            logging.error(e)
                            Msg = '%s工单撤销操作失败!' %work_number
                        else:
                            try:
                                # 钉钉通知工单撤销
                                text = ['### 工单撤销通知:',
                                        "> **工单号:%s**" % work_number,
                                        "> **撤销人:%s**" % g.user,
                                        ]
                                tools.dingding_msg(text, token=work_token)
                            except Exception as e:
                                logging.error(e)
                            # 消息通知工单撤销
                            Msg = '%s工单撤销成功!' % work_number
                            try:
                                # 邮件通知工单撤销
                                if Redis.exists('op_send_mail_html_%s' % work_number):
                                    mail_html = Redis.get('op_send_mail_html_%s' % work_number)
                                    msg = Message("%s工单撤销通知" % work_number, sender=sender, recipients=[receiver],
                                                  cc=[g.mail],charset='utf-8')
                                    alarm_html = '<p style="color:red">工单状态:申请人已主动撤销</p>'
                                    msg.html = '%s%s' %(mail_html, alarm_html)
                                    with app.app_context():
                                        mail.send(msg)
                            except Exception as e:
                                logging.error(e)
                    else:
                        Msg = '无效的工单撤销操作!'
                except Exception as e:
                    logging.error(e)
        users = db_sso.query.with_entities(db_sso.dingunionid, db_sso.realName).all()
        users = {info[0]: info[1:] for info in users}
        sql_executes = db_sql_execute.query.with_entities(db_sql_execute.work_number,db_sql_execute.date,
                                                      db_sql_execute.host,db_sql_execute.port,db_sql_execute.database,
                                                      db_sql_execute.describe,
                                                      db_sql_execute.dingid).filter(db_sql_execute.dingid==g.dingId).all()
        sql_executes = {info[0]: info[1:] for info in sql_executes}
        work_orders = db_work_order.query.with_entities(db_work_order.work_number,
                                                        db_work_order.dingid,
                                                        db_work_order.status).filter(and_(
            db_work_order.source == source,db_work_order.work_number.in_(sql_executes.keys()))).order_by(desc(db_work_order.id)).all()
        if action and work_number:
            if action == 'query':
                work_orders = db_work_order.query.with_entities(db_work_order.work_number,
                                                                db_work_order.dingid,
                                                                db_work_order.status).filter(and_(
                    db_work_order.source == source, db_work_order.work_number == int(work_number))).all()
        if work_orders:
            work_orders = [list(info) for info in work_orders]
            for info in work_orders:
                info.extend(sql_executes[info[0]][:-1])
                info.extend(users[sql_executes[info[0]][-1]])
    except Exception as e:
        logging.error(e)
    return render_template('sql_execute_list.html', tables=tables, work_orders=work_orders,Msg=Msg,total='SQL执行工单列表')

@page_work_order.route('/project_offline_list')
def project_offline_list():
    # 获取最新数据
    tables = ('工单号', '日期', '项目名称', '描述', '申请人', '详情','问题备注','工单状态')
    db_work_order = db_op.work_order
    db_project_offline = db_op.project_offline
    db_sso = db_op.user_sso
    db_work_comment = db_op.work_comment
    action = tools.http_args(request, 'action')
    work_number = tools.http_args(request, 'work_number')
    Msg = None
    work_orders = []
    source = 'ensure_project_offline'
    try:
        if action and work_number:
            if action == 'repeal':
                try:
                    val = db_work_order.query.filter(and_(db_work_order.work_number==int(work_number),
                                                          db_work_order.source == source,
                                                          db_work_order.status.in_(('未审核','未受理','已退回','已拒绝')))).all()
                    if val:
                        try:
                            #清除工单总表中相关工单信息
                            c = db_work_order.query.filter(db_work_order.work_number==int(work_number)).all()
                            for v in c:
                                db_op.DB.session.delete(v)
                                db_op.DB.session.commit()
                            # 清除工单详情表中相关工单信息
                            c = db_project_offline.query.filter(db_project_offline.work_number==int(work_number)).all()
                            for v in c:
                                db_op.DB.session.delete(v)
                                db_op.DB.session.commit()
                            # 清除工单问题备注表中相关工单信息
                            c = db_work_comment.query.filter(db_work_comment.work_number == int(work_number)).all()
                            for v in c:
                                db_op.DB.session.delete(v)
                                db_op.DB.session.commit()
                        except Exception as e:
                            logging.error(e)
                            Msg = '%s工单撤销操作失败!' %work_number
                        else:
                            try:
                                # 钉钉通知工单撤销
                                text = ['### 工单撤销通知:',
                                        "> **工单号:%s**" % work_number,
                                        "> **撤销人:%s**" % g.user,
                                        ]
                                tools.dingding_msg(text, token=work_token)
                            except Exception as e:
                                logging.error(e)
                            # 消息通知工单撤销
                            Msg = '%s工单撤销成功!' % work_number
                            try:
                                # 邮件通知工单撤销
                                if Redis.exists('op_send_mail_html_%s' % work_number):
                                    mail_html = Redis.get('op_send_mail_html_%s' % work_number)
                                    msg = Message("%s工单撤销通知" % work_number, sender=sender, recipients=[receiver],
                                                  cc=[g.mail],charset='utf-8')
                                    alarm_html = '<p style="color:red">工单状态:申请人已主动撤销</p>'
                                    msg.html = '%s%s' %(mail_html, alarm_html)
                                    with app.app_context():
                                        mail.send(msg)
                            except Exception as e:
                                logging.error(e)
                    else:
                        Msg = '无效的工单撤销操作!'
                except Exception as e:
                    logging.error(e)

        users = db_sso.query.with_entities(db_sso.dingunionid, db_sso.realName).all()
        users = {info[0]: info[1:] for info in users}
        projects = db_project_offline.query.with_entities(db_project_offline.work_number,
                                                          db_project_offline.date,
                                                          db_project_offline.project,
                                                          db_project_offline.describe,
                                                          db_project_offline.dingid).filter(db_project_offline.dingid==g.dingId).all()
        projects = {info[0]: info[1:] for info in projects}
        work_orders = db_work_order.query.with_entities(db_work_order.work_number,
                                                        db_work_order.dingid,
                                                        db_work_order.status).filter(and_(
            db_work_order.source == source,db_work_order.work_number.in_(projects.keys()))).order_by(desc(db_work_order.id)).all()
        if action and work_number:
            if action == 'query':
                work_orders = db_work_order.query.with_entities(db_work_order.work_number,
                                                                db_work_order.dingid,
                                                                db_work_order.status).filter(and_(
                    db_work_order.source == source, db_work_order.work_number == int(work_number))).all()
        if work_orders:
            work_orders = [list(info) for info in work_orders]
            for info in work_orders:
                info.extend(projects[info[0]][:-1])
                info.extend(users[projects[info[0]][-1]])
    except Exception as e:
        logging.error(e)
    return render_template('project_offline_list.html', tables=tables, work_orders=work_orders,Msg=Msg,total='项目下线工单列表')

@page_work_order.route('/other_work_list')
def other_work_list():
    tables = ('工单号', '日期', '事项标题', '事项描述', '申请人', '详情','问题备注','工单状态')
    db_work_order = db_op.work_order
    db_other_work = db_op.other_work
    db_sso = db_op.user_sso
    db_work_comment = db_op.work_comment
    action = tools.http_args(request, 'action')
    work_number = tools.http_args(request, 'work_number')
    Msg = None
    work_orders = []
    source = 'ensure_other_work'
    try:
        if action and work_number:
            if action == 'repeal':
                try:
                    val = db_work_order.query.filter(and_(db_work_order.work_number==int(work_number),
                                                          db_work_order.source==source,
                                                          db_work_order.status.in_(('未受理','已拒绝','未审核','已退回','审批拒绝','待审批')))).all()
                    if val:
                        try:
                            c = db_work_order.query.filter(db_work_order.work_number == int(work_number)).all()
                            for v in c:
                                db_op.DB.session.delete(v)
                                db_op.DB.session.commit()
                            c = db_other_work.query.filter(db_other_work.work_number == int(work_number)).all()
                            for v in c:
                                db_op.DB.session.delete(v)
                                db_op.DB.session.commit()
                            # 清除工单问题备注表中相关工单信息
                            c = db_work_comment.query.filter(db_work_comment.work_number == int(work_number)).all()
                            for v in c:
                                db_op.DB.session.delete(v)
                                db_op.DB.session.commit()
                        except Exception as e:
                            logging.error(e)
                            Msg = '%s工单撤销操作失败!' %work_number
                        else:
                            try:
                                # 钉钉通知工单撤销
                                text = ['### 工单撤销通知:',
                                        "> **工单号:%s**" % work_number,
                                        "> **撤销人:%s**" % g.user,
                                        ]
                                tools.dingding_msg(text, token=work_token)
                            except Exception as e:
                                logging.error(e)
                            # 消息通知工单撤销
                            Msg = '%s工单撤销成功!' % work_number
                            try:
                                # 邮件通知工单撤销
                                if Redis.exists('op_send_mail_html_%s' % work_number):
                                    mail_html = Redis.get('op_send_mail_html_%s' % work_number)
                                    receiver = Redis.get('op_other_work_receiver_%s' % work_number)
                                    msg = Message("%s工单撤销通知" % work_number, sender=sender, recipients=[receiver],
                                                  cc=[g.mail],charset='utf-8')
                                    alarm_html = '<p style="color:red">工单状态:申请人已主动撤销</p>'
                                    msg.html = '%s%s' %(mail_html, alarm_html)
                                    with app.app_context():
                                        mail.send(msg)
                            except Exception as e:
                                logging.error(e)
                    else:
                        Msg = '无效的工单撤销操作!'
                except Exception as e:
                    logging.error(e)
        users = db_sso.query.with_entities(db_sso.dingunionid, db_sso.realName).all()
        users = {info[0]: info[1:] for info in users}
        other_works = db_other_work.query.with_entities(db_other_work.work_number,db_other_work.date,db_other_work.title,
                                                         db_other_work.describe,
                                                         db_other_work.dingid).filter(db_other_work.dingid==g.dingId).all()
        other_works = {info[0]: info[1:] for info in other_works}
        work_orders = db_work_order.query.with_entities(db_work_order.work_number,
                                                        db_work_order.dingid,
                                                        db_work_order.status).filter(and_(
            db_work_order.source == source,db_work_order.work_number.in_(other_works.keys()))).order_by(desc(db_work_order.id)).all()
        if action and work_number:
            if action == 'query':
                work_orders = db_work_order.query.with_entities(db_work_order.work_number,
                                                                db_work_order.dingid,
                                                                db_work_order.status).filter(and_(
                    db_work_order.source == source, db_work_order.work_number == int(work_number))).all()
        if work_orders:
            work_orders = [list(info) for info in work_orders]
            for info in work_orders:
                info.extend(other_works[info[0]][:-1])
                info.extend(users[other_works[info[0]][-1]])
    except Exception as e:
        logging.error(e)
    return render_template('other_work_list.html', tables=tables, work_orders=work_orders, Msg=Msg,
                           total='其它事项申请工单列表')

@page_work_order.route('/work_order_show')
def work_order_show():
    try:
        # 工单流程进度
        INDEXS = {'待审批':2,'未审核':2,'已退回':2,'审批拒绝':2,'未受理':3,'审批通过':3,'受理中':3,'已拒绝':4,'已完成':4,'已回滚':4}
        db_work_order = db_op.work_order
        db_sso = db_op.user_sso
        infos = db_sso.query.with_entities(db_sso.dingunionid, db_sso.realName,db_sso.mail).all()
        users = {info[0]: info[1] for info in infos}
        mails = {info[-1]: info[1] for info in infos}
        work_lists = []
        if g.grade[0] == '0':
            infos = db_work_order.query.with_entities(db_work_order.work_number,
                                                      db_work_order.date,
                                                      db_work_order.source,
                                                      db_work_order.applicant,
                                                      db_work_order.reviewer,
                                                      db_work_order.dingid,
                                                      db_work_order.status).order_by(desc(db_work_order.date)).limit(500).all()
        else:
            infos = db_work_order.query.with_entities(db_work_order.work_number,
                                                      db_work_order.date,
                                                      db_work_order.source,
                                                      db_work_order.applicant,
                                                      db_work_order.reviewer,
                                                      db_work_order.dingid,
                                                      db_work_order.status).filter(db_work_order.applicant==g.dingId).limit(500).all()
        if infos:
            infos = [list(info) for info in infos]
            for info in infos:
                Infos=[]
                applicant = ''
                reviewer = mails[server_auth_leader]
                operater = ''
                Infos.extend(info[:3])
                if info[3] in users:
                    applicant = users[info[3]]
                if info[4] in mails:
                    reviewer = mails[info[4]]
                    if info[2] =='ensure_server_auth':
                        reviewer = '%s&%s'%(mails[info[4]],mails[server_auth_leader])
                if info[5] in users:
                    operater = users[info[5]]
                status = info[-1]
                if info[-1] in ('未审核','已退回'):
                    status = '%s%s' %(mails[info[4]],info[-1])
                if info[-1] in ('待审批','审批拒绝'):
                    status = '%s%s' %(mails[server_auth_leader],info[-1])
                Infos.append(["填写申请表", "申请人:%s"%applicant, "审核人:%s" %reviewer, "执行人:%s"%operater, "工单状态:%s" %status])
                Infos.append(INDEXS[info[-1]])
                work_lists.append(Infos)
        return render_template('work_order_show.html', work_lists=work_lists,work_types=work_types,source_types=source_types)
    except Exception as e:
        logging.error(e)

@page_work_order.before_request
@user_auth.login_required(grade=9)
def check_login(error=None):
    tools.Async_log(g.user, request.url)

@page_work_order.teardown_request
def db_remove(exception):
    db_op.DB.session.remove()