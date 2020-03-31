#-*- coding: utf-8 -*-
from flask_wtf import Form
from wtforms import StringField,BooleanField,SelectMultipleField,TextAreaField,SelectField,SubmitField,FileField,IntegerField,PasswordField
from wtforms.validators import DataRequired,Length
from flask_wtf import CsrfProtect
import redis
import datetime
from module import tools,db_idc,db_op,loging
from sqlalchemy import distinct,and_,desc
from flask_sqlalchemy import SQLAlchemy
from flask import Flask
app = Flask(__name__)
CsrfProtect(app)
DB = SQLAlchemy(app)
app.config.from_pyfile('../conf/redis.conf')
app.config.from_pyfile('../conf/sql.conf')
logging = loging.Error()
redis_host = app.config.get('REDIS_HOST')
redis_port = app.config.get('REDIS_PORT')
redis_password = app.config.get('REDIS_PASSWORD')
rc = redis.StrictRedis(host=redis_host, port=redis_port,decode_responses=True)
redis_data = app.config.get('REDIS_DATA')
RC_CLUSTER = redis.StrictRedis(host=redis_data, port=redis_port,decode_responses=True)
class MyFormInput(Form):
    text = TextAreaField(validators=[DataRequired()],id='text')
    text2 = TextAreaField(validators=[DataRequired()],id='text2')
    input = StringField('Input', validators=[DataRequired()],id='input')
    input2 = StringField('Input2', validators=[DataRequired()], id='input2')
    passwd = PasswordField('password', validators=[DataRequired()],id='passwd')
    submit = SubmitField('提交',id='btn1')

class MyFormServer(Form):
    select = SelectField(choices=[('hostname', '主机名'),('ip', 'ip地址'),('sn', 'sn号'),('cid', '机柜'),('status', '使用状态'),('buy_date', '购买日期')],id='select')
    show_pages = SelectField(choices=[(15,15),(30,30),(50,50),(70,70),(100,100)],id='show_pages')
    text = StringField(validators=[DataRequired()],id='input')
    submit = SubmitField('服务器查询',id='btn1')

class MyFormPublish(Form):
    package_url = TextAreaField(validators=[DataRequired()],id='package_url')
    package_md5 = StringField('md5', validators=[DataRequired()],id='package_md5')
    check_url = StringField('url', validators=[DataRequired()],id='check_url')
    package_type = SelectField(choices=[('full','整包'),('part','部分')],id='package_type')
    publish_type = SelectField(choices=[('batch','批量'),('step','逐台')],id='publish_type')
    restart = SelectField(choices=[('True', '是'), ('False', '否')],id='restart')
    execute = SelectField(choices=[('publish', '上线'), ('rollback', '回滚')],id='execute')
    gray = SelectField(choices=[(0, '否'), (1, '是')], id='gray')
    describe = TextAreaField(validators=[DataRequired()],id='describe')
    try:
        db_publish = db_op.publish_records
        projects = []
        versions = []
        vals = db_publish.query.with_entities(distinct(db_publish.project)).all()
        if vals:
            projects = [(val[0],val[0]) for val in vals if val]
            vals = db_publish.query.with_entities(distinct(db_publish.version)).filter(
                db_publish.project == projects[0][0]).order_by(desc(db_publish.version)).all()
            if vals:
                versions = [(val[0],val[0]) for val in vals]
        project = SelectField(choices=projects, id='project')
        version = SelectField(choices=versions, id='version')
    except Exception as e:
        logging.error(e)
    finally:
        db_op.DB.session.remove()
    submit = SubmitField('提交',id='btn1')

class MyFromResourcePool(Form):
    db_project = db_op.project_list
    db_server = db_idc.idc_servers
    try:
        source_type = SelectField(choices=[('self','自有资源'),('third','第三方资源')],id="source_type")
        hosts_add = db_project.query.with_entities(db_project.resource,db_project.app_port,db_project.ip,db_project.ssh_port).filter(db_project.status=='未分配').all()
        hosts = []
        for infos in hosts_add:
            info = [ str(info) for info in infos]
            hosts.append(info)
        hosts_add = [(':'.join(info),':'.join(info)) for info in hosts]
        hosts_add = SelectField(choices=hosts_add,id='hosts_add')
        projects = db_project.query.with_entities(distinct(db_project.project)).all()
        Project = SelectField(choices=[(str(project[0]),str(project[0])) for project in projects],id="projects")
        submit_query = SubmitField('查询', id='submit_query')
        submit_add = SubmitField('增加', id='submit_add')
        servers = db_server.query.with_entities(db_server.ip,db_server.ssh_port).filter(and_(db_server.status=='未使用')).all()
        servers = [('%s:%s' %info,'%s:%s' %info) for info in servers]
        servers = SelectField(choices=servers,id="select_server")
        resource = [('tomcat','tomcat'),('php','php'),('nginx','nginx'),('python','python')]
        resource = SelectField(choices=resource)
        app_port = StringField('app_port', validators=[DataRequired(), Length(1, 15)], id="app_port")
        pool_pages = SelectField(choices=[(15, 15), (30, 30), (50, 50), (70, 70), (100, 100)], id='pool_pages')
        submit_allot = SubmitField('预配', id='submit_allot')
        submit_lcok = SubmitField('加锁', id='submit_lock')
    except Exception as e:
        logging.error(e)
    finally:
        db_idc.DB.session.remove()
        db_op.DB.session.remove()

class MyFromThirdResource(Form):
        hosts = TextAreaField(validators=[DataRequired()])
        submit_recucle = SubmitField('回收', id='submit')

class MyFormAssetsManage(Form):
    db_idc_id = db_idc.idc_id
    text = TextAreaField(validators=[DataRequired()])
    try:
        db_values = db_idc_id.query.with_entities(distinct(db_idc_id.aid)).all()
        rack = StringField('rack', id='rack')
        purch = StringField('purch', validators=[DataRequired(), Length(1, 15)],id="purch")
        expird = StringField('expird', validators=[DataRequired(), Length(1, 15)],id="expird")
        idrac = StringField('idrac',id='idrac_down')
        select_aid = SelectField(choices=[(value[0],value[0]) for value in db_values])
        select_action = SelectField(choices=[('add','上架'),('modify','变更'),('down','下架'),('upload','批量')],id='select_action')
        select_device = SelectField(choices=[('server', '服务器'), ('network', '网络设备'), ('store', '存储设备')],id='select_device')
        device_type = StringField('device_type',id='devicetype')
        fault = BooleanField('维护中', default=False)
        old_host = StringField('old_host',id='old_host')
        submit = SubmitField('提交',id='btn1')
        File = FileField('File')
        upload = SubmitField('批量录入')
    finally:
        db_idc.DB.session.remove()

class MyFormApply(Form):
    try:
        db_permission = db_op.permission
        vals = db_permission.query.with_entities(db_permission.authid,db_permission.auth).all()
    finally:
        db_op.DB.session.remove()
    select = SelectField(choices= vals,id='select')
    submit = SubmitField('提交',id='btn1')

class MyFormDeploy(Form):
    try:
        db_project = db_op.project_list
        db_third = db_idc.third_resource
        db_busi = db_op.business
        Resources = []
        resource = db_project.query.with_entities(db_project.resource,db_project.ip,db_project.ssh_port,db_project.app_port).filter(db_project.status == '未分配').all()
        for source in resource:
            Resources.append([str(sour) for sour in source])
        resource = [(':'.join(source),':'.join(source)) for source in Resources]
        busis = db_busi.query.with_entities(db_busi.id,db_busi.business).all()
        select_busi = SelectField(choices=[busi for busi in busis])
        domain = StringField()
        business = StringField(validators=[DataRequired(), Length(1, 64)])
        describe = StringField(validators=[DataRequired(), Length(1,128)])
        person = StringField()
        contact = StringField()
        project = StringField(validators=[DataRequired(), Length(1, 64)])
        select_dev = SelectField(choices= [('java','java'),('php','php'),('python','python')])
        select_resource = SelectMultipleField(choices=resource)
        area_resource = TextAreaField(validators=[DataRequired()])
        submit = SubmitField('提交')
        Thirds = []
        third_vals = db_third.query.with_entities(db_third.resource_type, db_third.ip, db_third.ssh_port,db_third.app_port).filter(db_third.status == '未分配').all()
        for third in third_vals:
            Thirds.append([str(val) for val in third])
        thirds = [(':'.join(val), ':'.join(val)) for val in Thirds]
        select_third = SelectMultipleField(choices=thirds)
        department = StringField(validators=[DataRequired()])
        person = StringField(validators=[DataRequired(), Length(4, 64)])
        contact = StringField(validators=[DataRequired(), Length(4, 64)])
        submit_third = SubmitField('提交')
    finally:
        db_op.DB.session.remove()
        db_idc.DB.session.remove()

class FormData(Form):
    dms = []
    for i in range(7):
        dm = datetime.datetime.now() - datetime.timedelta(days=i)
        dm = dm.strftime('%Y-%m-%d')
        dms.append((dm,dm))
    select_date = SelectField(choices= dms,id='select_date')

class FormPlatformToken(Form):
    input = StringField('input_platform', validators=[DataRequired()],id='input_platform')
    input_date = StringField('input_date', validators=[DataRequired()])
    select_date = SelectField(choices=[(0,'永不'),(3,'3天'),(7,'7天'),(15,'15天'),(30,'30天'),(90,'90天'),(180,'180天')], id='select_date')

class FormResourceReport(Form):
    select = SelectField(choices=[(30,'近一个月'),(90,'近三个月'),(180,'近六个月'),(360,'近一年内')],id='select')

class FormResourceModify(Form):
    db_business = db_op.business
    business = []
    resource = StringField('input_resource', validators=[DataRequired()],id='resource')
    hosts = TextAreaField(validators=[DataRequired()],id='hosts')
    app_port = StringField('app_port', validators=[DataRequired()],id='app_port')
    source_type = SelectField(choices=[('非集群','非集群'),('集群模式','集群模式')], id='source_type')
    action = SelectField(choices=[('add', '新增'), ('del', '删除')], id='action')
    try:
        business = db_business.query.with_entities(db_business.id,db_business.business).all()
        business = [(int(busi[0]),busi[1]) for busi in business]
    except Exception as e:
        logging.error(e)
    finally:
        db_op.DB.session.remove()
    business.insert(0, (0, '--可选择--'))
    select_busi = SelectField(choices=business, id='select_busi')
    submit = SubmitField('提交')

class FormK8sContexts(Form):
    _,contexts,_ = tools.k8s_conf()
    select = SelectField(choices=[(context,context) for context in contexts],id='contexts')

class FormK8sDeploy(Form):
    try:
        db_project = db_op.project_list
        _, contexts, _ = tools.k8s_conf()
        contexts = SelectField(choices=[(context, context) for context in contexts], id='contexts')
        projects = StringField('projects', validators=[DataRequired()],id='projects')
        object = StringField('object', validators=[DataRequired()],id='object')
        version = StringField('version', validators=[DataRequired()], id='version')
        container_port = StringField('container_port',id='container_port')
        ingress_port = IntegerField('ingress_port', id='ingress_port')
        replicas = IntegerField('replicas', validators=[DataRequired()],id='replicas')
        dm_name = StringField('dm_name', validators=[DataRequired()],id='dm_name')
        docker_args = TextAreaField( id='docker_args')
        run_args = TextAreaField(validators=[DataRequired()], id='run_args')
        healthcheck = StringField('healthcheck',id='healthcheck')
        label_project = StringField('label_project', id='label_project')
        label_deploy = StringField('label_deploy', id='label_deploy')
        mount_path = StringField('mount_path', id='mount_path')
        mount_name = StringField('mount_name', id='mount_name')
        domain = StringField('domain', id='domain')
        sidecar = StringField('sidecar', id='sidecar')
        request_cpu = IntegerField('request_cpu',id='request_cpu')
        request_mem = IntegerField('request_mem',id='request_mem')
        limit_cpu = IntegerField('limit_cpu',id='limit_cpu')
        limit_mem = IntegerField('limit_mem',id='limit_mem')
        submit = SubmitField('提交', id='btn1')
    except Exception as e:
        logging.error(e)

class FormK8sUpdate(Form):
    try:
        _, contexts, _ = tools.k8s_conf()
        choices = [(context, context) for context in contexts]
        contexts = SelectField(choices=choices, id='contexts')
        deployment = SelectField(choices=[],id='deployment')
        version = SelectField(choices=[], id='version')
        action = SelectField(choices=[('update', '更新'), ('rollback', '回滚')], id='action')
        submit = SubmitField('提交', id='btn1')
    except Exception as e:
        logging.error(e)
    finally:
        db_op.DB.session.remove()

class FormK8sHpa(Form):
    try:
        _, contexts, _ = tools.k8s_conf()
        contexts = SelectField(choices=[(context, context) for context in contexts], id='contexts')
        deployment = SelectField(choices=[],id='deployment')
        max_replica = IntegerField(validators=[DataRequired()], id='max_replica')
        min_replica = IntegerField(validators=[DataRequired()],id='min_replica')
        cpu_value = IntegerField(validators=[DataRequired()],id='cpu_value')
        submit = SubmitField('提交', id='btn1')
    except Exception as e:
        logging.error(e)

class FormK8sIngress(Form):
    _, contexts, _ = tools.k8s_conf()
    contexts = SelectField(choices=[(context, context) for context in contexts], id='contexts')
    service_name = SelectField(choices=[], id='service_name')
    service_port = StringField(validators=[DataRequired()],id='service_port')
    domains = TextAreaField(validators=[DataRequired()],id='domains')
    submit = SubmitField('提交', id='btn1')

class MyFormApplication(Form):
    project = StringField(validators=[DataRequired()],id='project')
    tag = StringField(validators=[DataRequired()],id='tag')
    leader = StringField(validators=[DataRequired()], id='leader')
    git_url = StringField(validators=[DataRequired()],id='git_url')
    sql_run = BooleanField('sql_run',default=False,id='sql_run')
    nottest = BooleanField('nottest', default=False,id='nottest')
    test = BooleanField('test', default=False,id='test')
    product = BooleanField('product',validators=[DataRequired()], default=False,id='product')
    sql_database = StringField(id='sql_database')
    sql_describe = TextAreaField(id='sql_describe')
    sql_server = StringField(id='sql_server')
    sql_port = StringField(id='sql_port')
    sql_md5 = StringField(id='sql_md5')
    describe = TextAreaField(validators=[DataRequired()],id='describe')
    comment = TextAreaField(id='comment')
    sql_file = FileField('sql_file')
    submit = SubmitField('提交',id='btn1')

class MyFormSqlExecute(Form):
    leader = StringField(validators=[DataRequired()], id='leader')
    sql_database = StringField(validators=[DataRequired()],id='sql_database')
    sql_describe = TextAreaField(validators=[DataRequired()],id='sql_describe')
    sql_server = StringField(validators=[DataRequired()],id='sql_server')
    sql_port = StringField(validators=[DataRequired()],id='sql_port')
    sql_md5 = StringField(validators=[DataRequired()],id='sql_md5')
    sql_file = FileField('sql_file',validators=[DataRequired()])
    submit = SubmitField('提交',id='btn1')

class MyFormServerAuth(Form):
    leader = StringField(validators=[DataRequired()],id='leader')
    servers = TextAreaField(validators=[DataRequired()],id='servers')
    auth_level = StringField(validators=[DataRequired()],id='auth_level')
    purpose = TextAreaField(validators=[DataRequired()],id='purpose')
    submit = SubmitField('提交',id='btn1')

class MyFormProjectOffline(Form):
    project = StringField(validators=[DataRequired()],id='project')
    leader = StringField(validators=[DataRequired()], id='leader')
    git_url = StringField(validators=[DataRequired()],id='git_url')
    describe = TextAreaField(validators=[DataRequired()],id='describe')
    comment = TextAreaField(id='comment')
    submit = SubmitField('提交',id='btn1')

class MyFormOtherWork(Form):
    assign = SelectField(choices=[], id='assign')
    text = TextAreaField(validators=[DataRequired()],id='text')
    choices = [('OPS开通申请', 'OPS开通申请'),
               ('VPN开通申请', 'VPN开通申请'),
               ('后台权限申请', '后台权限申请'),
               ('大数据相关申请','大数据相关申请'),
               ('权限相关申请','权限相关申请'),
                ('新项目部署申请', '新项目部署申请'),
                ('其它事项申请','其它事项申请')]
    titles = SelectField(choices=choices, id='titles')
    input = StringField('input', validators=[DataRequired()], id='input')
    try:
        db_sso = db_op.user_sso
        choices = [('default','--默认--')]
        values = db_sso.query.with_entities(db_sso.realName,db_sso.mail).filter(db_sso.grade.like('1%')).all()
        choices.extend([(val[1], val[0]) for val in values])
        assign = SelectField(choices=choices, id='assign')
    except Exception as e:
        logging.error(e)
    finally:
        db_op.DB.session.remove()
    submit = SubmitField('提交',id='btn1')

class MyFormWorkOrderReport(Form):
    input = StringField('input', validators=[DataRequired()],id='input')
    input2 = StringField('input2', validators=[DataRequired()], id='input2')
    choices = [('all_order','---全部工单---'),('ensure_application','代码上线工单'),('ensure_server_auth','机器权限工单'),('ensure_sql_execute','SQL执行工单'),('ensure_project_offline','项目下线工单'),
                ('ensure_other_work','其它事项工单')]
    titles = SelectField(choices=choices, id='titles')
    submit = SubmitField('生成报告',id='btn1')