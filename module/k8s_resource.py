#-*- coding: utf-8 -*-
from module import loging,tools,db_op
from kubernetes import client
import os
import shutil
import tarfile
import docker
import oss2
import time
import redis
from sqlalchemy import and_,desc
from flask_sqlalchemy import SQLAlchemy
from flask import Flask,g
from sqlalchemy import distinct
app = Flask(__name__)
DB = SQLAlchemy(app)
app.config.from_pyfile('../conf/redis.conf')
app.config.from_pyfile('../conf/docker.conf')
app.config.from_pyfile('../conf/tokens.conf')
app.config.from_pyfile('../conf/oss.conf')
redis_host = app.config.get('REDIS_HOST')
redis_port = app.config.get('REDIS_PORT')
redis_password = app.config.get('REDIS_PASSWORD')
docker_user = app.config.get('USER')
docker_password = app.config.get('PASSWORD')
docker_base_url = app.config.get('BASE_URL')
dockerfile_path = app.config.get('DOCKERFILE_PATH')
ops_token = app.config.get('OPS_TOKEN')
oss_id = app.config.get('OSS_ID')
oss_key = app.config.get('OSS_KEY')
oss_url = app.config.get('OSS_URL')
Redis = redis.StrictRedis(host=redis_host, port=redis_port,decode_responses=True)
logging = loging.Error()
config,contexts,config_file = tools.k8s_conf()
flow_number = time.strftime('%Y%m%d%H%M%S',time.localtime())
#流水日志记录
def _flow_log(Msg):
    try:
        logpath = "/opt/k8s/flow_logs"
        tm = time.strftime('%Y-%m-%d %H:%M:%S',time.localtime())
        if not os.path.exists(logpath):
            os.system("/bin/mkdir -p %s" %logpath)
        flow_log_ptah = "%s/%s.log" %(logpath,flow_number)
        with open(flow_log_ptah,'a+') as f:
            f.write("%s  %s\n"%(tm,str(Msg)))
    except Exception as e:
        logging.error(e)

def download_war(object,version,docker_args,run_args,redis_key):
    #下载对应项目的最新代码包
    try:
        #包名需要规范
        Files = tools.get_k8s_packages()
        project_file = object
        object = object.split('.')
        dm_name = object[0]
        package_name = object[0]
        if dm_name in Files:
            project_file = Files[dm_name]
            package_name = project_file.split('.')[0]
        dm_type = project_file.split('.')[-1]
        if len(project_file.split('.')) >2:
            dm_type = '.'.join(project_file.split('.')[1:])
        package = '%s-%s.%s'% (package_name, version, dm_type)
        project_path = '%s/%s/%s' % (dockerfile_path, dm_name, project_file)
        if not os.path.exists(project_path):
            try:
                Redis.lpush(redis_key, '%s package download from oss ......' %package)
                _flow_log('%s package download from oss ......' %package)
                auth = oss2.Auth(oss_id, oss_key)
                bucket = oss2.Bucket(auth, oss_url, 'ops')
                oss_project_path = None
                try:
                    if not os.path.exists('%s/%s' %(dockerfile_path,dm_name)):
                        os.mkdir('%s/%s' %(dockerfile_path,dm_name))
                    for obj in oss2.ObjectIterator(bucket):
                        if obj.key.endswith('.war') or obj.key.endswith('.tar.gz') or obj.key.endswith('.jar'):
                            obj_name = obj.key.split('/')[-1].replace('_','-')
                            if obj_name.startswith(package_name) and version in obj_name:
                                oss_project_path = obj.key
                                break
                except Exception as e:
                    logging.error(e)
                if oss_project_path:
                    #尝试3次下载
                    for i in range(3):
                        try:
                            oss2.resumable_download(bucket,oss_project_path, project_path)
                            break
                        except:
                            continue
                else:
                    Redis.lpush(redis_key, '%s package not fond!' %package)
                    _flow_log('%s package not fond!' %package)
                    return False
            except Exception as e:
                logging.error(e)
        if os.path.exists(project_path):
            try:
                Redis.lpush(redis_key, '检测到文件%s' % project_path)
                _flow_log('检测到文件%s' % project_path)
                if project_file.endswith('.tar.gz'):
                    project_file = project_file.split('.')[0]
                    os.chdir('%s/%s/' % (dockerfile_path,dm_name))
                    tar = tarfile.open(project_path.split('/')[-1], 'r')
                    tar.extractall()
                    tar.close()
                    for file in os.listdir('./'):
                        if dm_name in file and not file.endswith('.tar.gz'):
                            shutil.move(file,project_file)
                    if os.path.exists(dm_name):
                        os.remove(project_path.split('/')[-1])
                #生成dockerfile文件
                dockerfile = '%s/%s/Dockerfile' %(dockerfile_path,dm_name)
                if os.path.exists(dockerfile):
                    os.remove(dockerfile)
                with open(dockerfile, 'a+') as f:
                    with open('%s/../conf/dockerfile_%s.template'%(app.root_path,dm_type)) as F:
                        for line in F:
                            if '<PROJECT>' in line:
                                line = line.replace('<PROJECT>',project_file)
                            f.write('%s\n'%line)
                    if docker_args:
                        for line in docker_args:
                            f.write('%s\n' % line)
                    for line in ("COPY ./run.sh /opt/",
                                 "RUN chmod +x /opt/run.sh",
                                 "ENV  LC_ALL en_US.UTF-8",
                                 "CMD  /opt/run.sh"):
                        f.write('%s\n' % line)
                        #生成docker_run启动脚本文件
                if run_args:
                    runfile = '%s/%s/run.sh' % (dockerfile_path, dm_name)
                    if os.path.exists(runfile):
                        os.remove(runfile)
                    with open(runfile, 'a+') as f:
                        with open('%s/../conf/docker_run.template' %app.root_path) as F:
                            for line in F:
                                f.write('%s\n' % line)
                        for line in run_args:
                            f.write('%s\n' % line)
                Redis.lpush(redis_key, '%s package download success!' %package)
                _flow_log('%s package download success!' %package)
                return package
            except Exception as e:
                logging.error(e)
        else:
            Redis.lpush(redis_key, '%s package download fail!' %package)
            _flow_log('%s package download fail!' %package)
            return False
    except Exception as e:
        logging.error(e)

def make_image(image,redis_key):
    try:
        Redis.lpush(redis_key, 'start build image %s......' % image)
        _flow_log('start build image %s......' % image)
        project = image.split('/')[-1].split(':')[0]
        dockerfile = "%s/%s" %(dockerfile_path,project)
        if os.path.exists(dockerfile):
            try:
                client = docker.APIClient(base_url=docker_base_url)
                response = [line for line in client.build(path=dockerfile, rm=True, tag=image)]
                result = eval(response[-1])
                if 'Successfully' in str(result):
                    Redis.lpush(redis_key,"docker build %s success!" %image)
                    _flow_log("docker build %s success!" %image)
                else:
                    Redis.lpush(redis_key,'fail:%s'%result)
                    _flow_log('fail:%s'%result)
                    return False
            except Exception as e:
                logging.error(e)
                if 'BaseException' not in str(e):
                    Redis.lpush(redis_key, 'fail:%s' % e)
                    _flow_log('fail:%s' % e)
            else:
                try:
                    Files = tools.get_k8s_packages()
                    response = [line for line in client.push(image, stream=True,auth_config={'username':docker_user,'password':docker_password})]
                    result = eval(response[-1])['aux']['Tag']
                    version = image.split(':')[-1]
                    if version == result:
                        #删除代码包
                        for file in os.listdir(dockerfile):
                            if Files[project].split('.')[0] in file:
                                try:
                                    os.remove('%s/%s' % (dockerfile,file))
                                except:
                                    shutil.rmtree('%s/%s' % (dockerfile,file))
                        Redis.lpush(redis_key,"docker push %s success!" % image)
                        _flow_log("docker push %s success!" % image)
                        return True
                    else:
                        Redis.lpush(redis_key, 'fail:%s' %result)
                        _flow_log('fail:%s' %result)
                        return False
                except Exception as e:
                    logging.error(e)
                    Redis.lpush(redis_key, 'fail:%s' %e)
                    _flow_log('fail:%s' %e)
                    return False
        else:
            Redis.lpush(redis_key,'dockerfile %s path not exists!' %dockerfile, 'fail')
            _flow_log('dockerfile %s path not exists!' %dockerfile)
            return False
    except Exception as e:
        logging.error(e)
        if 'BaseException' not in str(e):
            Redis.lpush(redis_key, 'fail:%s' % e)
            _flow_log('fail:%s' % e)
        return False

class k8s_object(object):
    def __init__(self,context,dm_name,image,container_port,replicas,mounts,labels,healthcheck,sidecar,re_requests=None,re_limits=None):
        config.load_kube_config(config_file, context=context)
        self.namespace = "default"
        self.context = context
        self.config_file = config_file
        self.dm_name = dm_name
        self.image = image
        self.container_port = container_port
        self.replicas = replicas
        self.mounts = mounts
        self.labels = labels
        self.healthcheck = healthcheck
        self.sidecar = sidecar
        self.re_requests = {'cpu':1,'memory': '2G'}
        self.re_limits = {'cpu':2,'memory': '4G'}
        if re_requests and re_limits:
            self.re_requests = re_requests
            self.re_limits = re_limits
    def export_deployment(self):
        # Configureate Pod template container
        volume_mounts = []
        containers = []
        volumes = []
        ports = []
        liveness_probe = None
        readiness_probe = None
        volume_mounts.append(client.V1VolumeMount(mount_path='/docker/logs', name='logs'))
        volumes.append(client.V1Volume(name='logs',
                                       host_path=client.V1HostPathVolumeSource(path='/opt/logs',
                                                                               type='DirectoryOrCreate')))
        if self.mounts:
            for path in self.mounts:
                volume_mounts.append(client.V1VolumeMount(mount_path=path, name=self.mounts[path]))
                volumes.append(client.V1Volume(name=self.mounts[path],
                                               host_path=client.V1HostPathVolumeSource(path=path,
                                                                                       type='DirectoryOrCreate')))
        if self.container_port:
            ports = [client.V1ContainerPort(container_port=int(port)) for port in self.container_port]
            liveness_probe = client.V1Probe(initial_delay_seconds=15,
                                            tcp_socket=client.V1TCPSocketAction(port=int(self.container_port[0])))
            readiness_probe = client.V1Probe(initial_delay_seconds=15,
                                             tcp_socket=client.V1TCPSocketAction(port=int(self.container_port[0])))
            if self.healthcheck:
                liveness_probe = client.V1Probe(initial_delay_seconds=15,
                                                http_get=client.V1HTTPGetAction(path=self.healthcheck,
                                                                                port=int(self.container_port[0])))
                readiness_probe = client.V1Probe(initial_delay_seconds=15,
                                                 http_get=client.V1HTTPGetAction(path=self.healthcheck,
                                                                                 port=int(self.container_port[0])))
        Env = [client.V1EnvVar(name='LANG', value='en_US.UTF-8'),
                 client.V1EnvVar(name='LC_ALL', value='en_US.UTF-8'),
                 client.V1EnvVar(name='POD_NAME',value_from=client.V1EnvVarSource(
                     field_ref=client.V1ObjectFieldSelector(field_path='metadata.name'))),
                 client.V1EnvVar(name='POD_IP', value_from=client.V1EnvVarSource(
                     field_ref=client.V1ObjectFieldSelector(field_path='status.podIP'))),
                 ]
        container = client.V1Container(
            name=self.dm_name,
            image=self.image,
            ports=ports,
            image_pull_policy='Always',
            env=Env,
            resources=client.V1ResourceRequirements(limits=self.re_limits,
                                                    requests=self.re_requests),
            volume_mounts=volume_mounts
        )
        if liveness_probe and readiness_probe:
            container = client.V1Container(
                name=self.dm_name,
                image=self.image,
                ports=ports,
                image_pull_policy='Always',
                env=Env,
                resources=client.V1ResourceRequirements(limits=self.re_limits,
                                                        requests=self.re_requests),
                volume_mounts=volume_mounts,
                liveness_probe=liveness_probe,
                readiness_probe=readiness_probe
            )
        containers.append(container)
        if self.sidecar:
            sidecar_container = client.V1Container(
                name= 'sidecar-%s' %self.dm_name,
                image = self.sidecar,
                image_pull_policy='Always',
                env=Env,
                resources=client.V1ResourceRequirements(limits=self.re_limits,
                                                        requests=self.re_requests),
                volume_mounts=volume_mounts)
            containers.append(sidecar_container)
        # Create and configurate a spec section
        secrets = client.V1LocalObjectReference('registrysecret')
        preference_key = self.dm_name
        project_values = ['']
        host_aliases = []
        db_docker_hosts = db_op.docker_hosts
        values = db_docker_hosts.query.with_entities(db_docker_hosts.ip,db_docker_hosts.hostname).filter(and_(
            db_docker_hosts.deployment==self.dm_name,db_docker_hosts.context==self.context)).all()
        db_op.DB.session.remove()
        if values:
            ips = []
            for value in values:
                try:
                    ip,hostname = value
                    key = "op_docker_hosts_%s" %ip
                    Redis.lpush(key,hostname)
                    ips.append(ip)
                except Exception as e:
                    logging.error(e)
            for ip in set(ips):
                try:
                    key = "op_docker_hosts_%s" % ip
                    if Redis.exists(key):
                        hostnames = Redis.lrange(key,0,-1)
                        if hostnames:
                            host_aliases.append(client.V1HostAlias(hostnames=hostnames,ip=ip))
                    Redis.delete(key)
                except Exception as e:
                    logging.error(e)
        if self.labels:
            if 'deploy' in self.labels:
                preference_key = self.labels['deploy']
            if 'project' in self.labels:
                project_values = [self.labels['project']]
        template = client.V1PodTemplateSpec(
            metadata=client.V1ObjectMeta(labels={"project": self.dm_name}),
            spec=client.V1PodSpec(containers=containers,
              image_pull_secrets=[secrets],
              volumes=volumes,
              host_aliases = host_aliases,
              affinity=client.V1Affinity(
                  node_affinity=client.V1NodeAffinity(
                      preferred_during_scheduling_ignored_during_execution = [
                          client.V1PreferredSchedulingTerm(
                              preference=client.V1NodeSelectorTerm(
                                  match_expressions=[client.V1NodeSelectorRequirement(
                                      key=preference_key,
                                      operator='In', values=['mark'])
                                  ]), weight=100)],
                      required_during_scheduling_ignored_during_execution=client.V1NodeSelector(node_selector_terms=[
                          client.V1NodeSelectorTerm(match_expressions=[
                              client.V1NodeSelectorRequirement(
                                  key='project',operator='In',values=project_values)])
                      ])
              )))
        )
        selector = client.V1LabelSelector(match_labels={"project": self.dm_name})
        # Create the specification of deployment
        spec = client.ExtensionsV1beta1DeploymentSpec(
            replicas=int(self.replicas),
            template=template,
            selector=selector,
            min_ready_seconds=3
        )
        # Instantiate the deployment object
        deployment = client.ExtensionsV1beta1Deployment(
            api_version="extensions/v1beta1",
            kind="Deployment",
            metadata=client.V1ObjectMeta(name=self.dm_name),
            spec=spec)
        return deployment

    def export_service(self,node_port):
        ports = [client.V1ServicePort(port=int(port), target_port=int(port)) for port in self.container_port]
        spec = client.V1ServiceSpec(ports=ports, selector={'project': self.dm_name},type='ClusterIP')
        if node_port:
            ports = [client.V1ServicePort(port=int(self.container_port[0]), target_port=int(self.container_port[0]),node_port=int(node_port))]
            spec = client.V1ServiceSpec(ports=ports,selector={'project':self.dm_name},type='NodePort')
        service = client.V1Service(
            api_version = 'v1',
            kind = 'Service',
            metadata=client.V1ObjectMeta(name=self.dm_name),
            spec=spec)
        return service

    def export_ingress(self,domains,ingress_port):
        try:
            db_ingress = db_op.k8s_ingress
            # ingress信息写入数据库
            for ingress_domain in domains:
                path = None
                if '/' in ingress_domain:
                    ingress_domain = ingress_domain.split('/')[0]
                    path = '/{}'.format('/'.join(ingress_domain.split('/')[1:]))
                v = db_ingress(name='nginx-ingress',context=self.context, namespace=self.namespace, domain=ingress_domain, path=path,
                               serviceName=self.dm_name, servicePort=int(ingress_port))
                db_op.DB.session.add(v)
                db_op.DB.session.commit()
        except Exception as e:
            logging.error(e)
        else:
            # 从数据库读取ingress信息
            api_instance = client.ExtensionsV1beta1Api()
            Rules = []
            domain_infos = db_ingress.query.with_entities(distinct(db_ingress.domain)).filter(db_ingress.context==self.context).all()
            domain_infos = [domain[0] for domain in domain_infos]
            for domain in domain_infos:
                paths = []
                Rules_infos = db_ingress.query.with_entities(db_ingress.path,
                                                             db_ingress.serviceName, db_ingress.servicePort
                                                             ).filter(and_(db_ingress.domain == domain,
                                                                           db_ingress.context == self.context)).all()
                for infos in Rules_infos:
                    path, serviceName, servicePort = infos
                    if path:
                        paths.append(client.V1beta1HTTPIngressPath(client.V1beta1IngressBackend(
                            service_name=serviceName,
                            service_port=int(servicePort)
                        ), path=path))
                if paths:
                    Rules.append(client.V1beta1IngressRule(host=domain,
                                                           http=client.V1beta1HTTPIngressRuleValue(
                                                               paths=paths)))
                else:
                    if Rules_infos:
                        path, serviceName, servicePort = Rules_infos[0]
                        Rules.append(client.V1beta1IngressRule(host=domain,
                                                               http=client.V1beta1HTTPIngressRuleValue(
                                                                   paths=[client.V1beta1HTTPIngressPath(
                                                                       client.V1beta1IngressBackend(
                                                                           service_name=serviceName,
                                                                           service_port=int(servicePort)
                                                                       ))])
                                                               ))
            spec = client.V1beta1IngressSpec(rules=Rules)
            ingress = client.V1beta1Ingress(
                api_version='extensions/v1beta1',
                kind='Ingress',
                metadata=client.V1ObjectMeta(name='nginx-ingress',
                                             namespace=self.namespace,
                                             annotations={'kubernetes.io/ingress.class': 'nginx'}), spec=spec)
            api_instance.patch_namespaced_ingress(body=ingress, namespace=self.namespace, name='nginx-ingress')
            return True
        finally:
            db_op.DB.session.remove()

    def delete_deployment(self):
        try:
            api_instance = client.ExtensionsV1beta1Api()
            body = client.V1DeleteOptions(propagation_policy='Foreground', grace_period_seconds=5)
            api_instance.delete_namespaced_deployment(name=self.dm_name, namespace=self.namespace, body=body)
            return True
        except Exception as e:
            logging.error(e)
            return False

    def delete_service(self):
        try:
            api_instance = client.CoreV1Api()
            body = client.V1DeleteOptions(propagation_policy='Foreground', grace_period_seconds=5)
            api_instance.delete_namespaced_service(name=self.dm_name, namespace=self.namespace, body=body)
            return True
        except Exception as e:
            logging.error(e)
            return False

    def delete_ingress(self):
        try:
            db_ingress = db_op.k8s_ingress
            v = db_ingress.query.filter(and_(db_ingress.serviceName==self.dm_name,db_ingress.context==self.context)).all()
            if v:
                for c in v:
                    db_op.DB.session.delete(c)
            return True
        except Exception as e:
            logging.error(e)
            return False
        finally:
            db_op.DB.session.remove()

    def delete_hpa(self):
        try:
            api_instance = client.AutoscalingV2beta2Api()
            body = client.V1DeleteOptions(propagation_policy='Foreground', grace_period_seconds=5)
            api_instance.delete_namespaced_horizontal_pod_autoscaler(name=self.dm_name, namespace=self.namespace, body=body)
            return True
        except Exception as e:
            logging.error(e)
            return False

def check_pod(context,dm_name,replicas,old_pods,redis_key):
    namespace = "default"
    config.load_kube_config(config_file,context)
    api_instance = client.CoreV1Api()
    # 判断pod是否部署成功
    try:
        Redis.lpush(redis_key, 'POD更新检查,大约耗时1-2分钟......')
        _flow_log('POD更新检查,大约耗时1-2分钟......')
        phases = []
        phase_count = 0
        for t in range(12):
            ret = api_instance.list_namespaced_pod(namespace=namespace)
            if ret:
                for i in ret.items:
                    if i.metadata.name.startswith(dm_name) and i.metadata.name not in old_pods:
                        if i.status.container_statuses:
                            if i.status.container_statuses[-1].state.running:
                                phase = 'Running'
                                phases.append(phase)
            if len(phases) >phase_count:
                Redis.lpush(redis_key, 'POD已更新数量:%s' %len(phases))
                _flow_log('POD已更新数量:%s' %len(phases))
            if len(phases) >= int(replicas):
                break
            phase_count = len(phases)
            phases = []
            time.sleep(10)
        if len(phases) < int(replicas):
            Redis.lpush(redis_key, 'POD更新检测异常!')
            _flow_log('POD更新检测异常!')
        else:
            Redis.lpush(redis_key, 'POD更新检测正常!')
            _flow_log('POD更新检测正常!')
            return True
    except Exception as e:
        logging.error(e)
        _flow_log(e)
    return False

def delete_pod(context,dm_name):
    try:
        namespace = "default"
        if dm_name:
            config.load_kube_config(config_file,context)
            api_instance = client.CoreV1Api()
            ret = api_instance.list_namespaced_pod(namespace=namespace)
            for i in ret.items:
                if '-'.join(i.metadata.name.split('-')[:-2]) in dm_name:
                    api_instance.delete_namespaced_pod(name=i.metadata.name,
                                                       namespace=namespace,
                                                       body=client.V1DeleteOptions())
        return True
    except Exception as e:
        logging.error(e)
        return False

def api_delete_pod(args):
    try:
        context, dm_name = args
        delete_pod(context, dm_name)
    except Exception as e:
        logging.error(e)

def object_deploy(args):
    try:
        namespace = "default"
        (context,project, object, version, image, docker_args,run_args, container_port, ingress_port, replicas,
     domain, re_requests, mounts,labels,healthcheck, sidecar, re_limits, redis_key,user) = args
    except Exception as e:
        logging.error(e)
    else:
        try:
            dm_name = object.split('.')[0]
            db_k8s = db_op.k8s_deploy
            values = db_k8s.query.filter(and_(db_k8s.image == image,db_k8s.context==context)).all()
            if values:
                _flow_log('%s image already exists!' %image)
                raise Redis.lpush(redis_key, '%s image already exists!' %image)
            war = download_war(object,version,docker_args,run_args,redis_key)
            if war:
                # 制作docker镜像并上传至仓库
                if make_image(image,redis_key):
                    db_docker_run = db_op.docker_run
                    #部署deployment
                    Redis.lpush(redis_key,'start deploy deployment %s......' %dm_name)
                    _flow_log('start deploy deployment %s......' %dm_name)
                    k8s = k8s_object(context,dm_name, image, container_port, replicas,mounts,
                                     labels,healthcheck,sidecar,re_requests,re_limits)
                    api_instance = client.ExtensionsV1beta1Api()
                    try:
                        deployment = k8s.export_deployment()
                        api_instance.create_namespaced_deployment(body=deployment, namespace=namespace)
                    except Exception as e:
                        logging.error(e)
                        Redis.lpush(redis_key, 'fail:%s' % e)
                        _flow_log('fail:%s' % e)
                    else:
                        try:
                            Redis.lpush(redis_key, '......deploy deployment success!')
                            _flow_log('......deploy deployment success!')
                            old_pods = []
                            if check_pod(context,dm_name, replicas,old_pods,redis_key):
                                if container_port:
                                    #部署service
                                    try:
                                        Redis.lpush(redis_key, 'start deploy service %s......' % dm_name)
                                        _flow_log('start deploy service %s......' % dm_name)
                                        node_port = None
                                        if ingress_port and not domain and len(container_port) == 1:
                                            node_port = ingress_port
                                        service = k8s.export_service(node_port)
                                        api_instance = client.CoreV1Api()
                                        api_instance.create_namespaced_service(body=service,namespace=namespace)
                                    except Exception as e:
                                        logging.error(e)
                                        if 'BaseException' not in str(e):
                                            Redis.lpush(redis_key, 'fail:%s' % e)
                                            _flow_log('fail:%s' % e)
                                    else:
                                        #部署ingress
                                        Redis.lpush(redis_key, '......deploy service success!')
                                        _flow_log('......deploy service success!')
                                        if ingress_port and domain:
                                            Domains = [domain]
                                            if ',' in domain:
                                                Domains = [domain.strip() for domain in domain.split(',') if domain]
                                            Redis.lpush(redis_key,'start deploy ingress %s......' % domain)
                                            _flow_log('start deploy ingress %s......' % domain)
                                            if not k8s.export_ingress(domains=Domains,ingress_port=int(ingress_port)):
                                                raise Redis.lpush(redis_key, 'deploy ingress fail')
                                            else:
                                                Redis.lpush(redis_key, '......deploy ingress success!')
                                                _flow_log('......deploy ingress success!')
                                try:
                                    # 部署日志记录
                                    if container_port:
                                        container_port = ','.join([str(port) for port in container_port])
                                    v = db_k8s(project=project,context=context, deployment=dm_name, image=image,war = war,
                                               container_port=container_port,
                                               replicas=replicas,
                                               re_requests=str(re_requests).replace("'",'"'),
                                               re_limits=str(re_limits).replace("'",'"'), action='create',
                                               healthcheck=healthcheck,
                                               update_date=time.strftime('%Y-%m-%d', time.localtime()),
                                               update_time=time.strftime('%H:%M:%S', time.localtime()),
                                               user=user)
                                    db_op.DB.session.add(v)
                                    db_op.DB.session.commit()
                                    #记录docker启动参数
                                    if docker_args:
                                        docker_args = str(docker_args)
                                    v = db_docker_run(deployment=dm_name,context=context,dockerfile=docker_args,
                                                      run_args=str(run_args),side_car=sidecar)
                                    db_op.DB.session.add(v)
                                    db_op.DB.session.commit()
                                except Exception as e:
                                    logging.error(e)
                                    if 'BaseException' not in str(e):
                                        Redis.lpush(redis_key, 'fail:%s' % e)
                                        _flow_log('fail:%s' % e)
                            else:
                                #自动删除deployment
                                k8s.delete_deployment()
                                Redis.lpush(redis_key,"......create deployment %s fail!" %dm_name)
                                _flow_log("......create deployment %s fail!" %dm_name)
                        except Exception as e:
                            logging.error(e)
                            if 'BaseException' not in str(e):
                                Redis.lpush(redis_key, 'fail:%s' % e)
                                _flow_log('fail:%s' % e)
        except Exception as e:
            logging.error(e)
            if 'BaseException' not in str(e):
                Redis.lpush(redis_key, 'fail:%s' % e)
                _flow_log('fail:%s' % e)
        finally:
            db_op.DB.session.remove()
            Redis.lpush(redis_key,'_End_')
            _flow_log('_End_')

def object_update(args):
    try:
        db_k8s = db_op.k8s_deploy
        db_docker_run = db_op.docker_run
        namespace = "default"
        mounts = None
        text = None
        labels = None
        allcontexts = []
        context,new_image,version,rollback,redis_key,channel,user = args
        dm_name = new_image.split('/')[-1].split(':')[0]
        # 获取已部署镜像部署信息
        values = db_k8s.query.with_entities(db_k8s.project, db_k8s.container_port, db_k8s.image, db_k8s.war,
                                            db_k8s.replicas, db_k8s.re_requests, db_k8s.re_limits,
                                            db_k8s.healthcheck).filter(and_(
            db_k8s.deployment == dm_name, db_k8s.action != 'delete')).order_by(desc(db_k8s.id)).limit(1).all()
        project, container_port, image, war, replicas, re_requests, re_limits, healthcheck = values[0]
    except Exception as e:
        logging.error(e)
    else:
        try:
            if new_image and redis_key:
                try:

                    vals = db_docker_run.query.with_entities(db_docker_run.dockerfile,db_docker_run.run_args,db_docker_run.side_car).filter(and_(
                        db_docker_run.deployment==dm_name,db_docker_run.context==context)).all()
                    docker_args,run_args,sidecar = vals[0]
                    if docker_args:
                        docker_args = eval(docker_args)
                    if run_args:
                        run_args = eval(run_args)
                except Exception as e:
                    logging.error(e)
                else:
                    if not rollback:
                        war = download_war(dm_name,version,docker_args,run_args,redis_key)
                        if not war:
                            _flow_log("params error,update fail!")
                            raise Redis.lpush(redis_key, "params error,update fail!")
                        if not make_image(new_image,redis_key):
                            _flow_log("image record not exists,update fail!")
                            raise Redis.lpush(redis_key, "image record not exists,update fail!")
                    try:
                        re_requests = eval(re_requests)
                        re_limits = eval(re_limits)
                        allcontexts.append(context)
                        if 'all-cluster' in context:
                            allcontexts = contexts
                        for context in allcontexts:
                            _flow_log('开始更新 %s image %s   ......' % (context,new_image))
                            Redis.lpush(redis_key, '*'*80)
                            Redis.lpush(redis_key, '开始更新 %s image %s   ......' % (context,new_image))
                            k8s = k8s_object(context,dm_name, image, container_port.split(','), replicas,mounts,labels,healthcheck,sidecar,re_requests,re_limits)
                            deployment = k8s.export_deployment()
                            # Update container image
                            deployment.spec.template.spec.containers[0].image = new_image
                            # Update the deployment
                            try:
                                api_instance = client.CoreV1Api()
                                ret = api_instance.list_namespaced_pod(namespace=namespace)
                                old_pos = [i.metadata.name for i in ret.items if i.metadata.name.startswith(dm_name)]
                                api_instance = client.ExtensionsV1beta1Api()
                                api_instance.patch_namespaced_deployment(name=dm_name, namespace=namespace,
                                                                     body=deployment)
                            except Exception as e:
                                logging.error(e)
                                _flow_log('deployment parameter fail!')
                                Redis.lpush(redis_key,'deployment parameter fail!')
                            else:
                                if rollback:
                                    action = 'rollback'
                                    _flow_log('开始进行回滚后的结果验证......')
                                    Redis.lpush(redis_key, '开始进行回滚后的结果验证......')
                                else:
                                    action = 'update'
                                    _flow_log('开始进行更新后的结果验证......')
                                    Redis.lpush(redis_key, '开始进行更新后的结果验证......')
                                if check_pod(context,dm_name, replicas,old_pos,redis_key):
                                    v = db_k8s(project=project,context=context,deployment=dm_name, image=new_image,war=war,
                                               container_port=container_port,
                                               replicas=replicas, re_requests=str(re_requests).replace("'", '"'),
                                               re_limits=str(re_limits).replace("'", '"'), action=action,
                                               healthcheck=healthcheck,
                                               update_date=time.strftime('%Y-%m-%d', time.localtime()),
                                               update_time=time.strftime('%H:%M:%S', time.localtime()),
                                               user=user)
                                    db_op.DB.session.add(v)
                                    db_op.DB.session.commit()
                                    if rollback:
                                        _flow_log('%s 镜像回滚成功!' % new_image)
                                        Redis.lpush(redis_key, '%s 镜像回滚成功!' % new_image)
                                    else:
                                        _flow_log('%s 镜像更新成功!' % new_image)
                                        Redis.lpush(redis_key, '%s 镜像更新成功!' % new_image)
                                    if channel == 'api':
                                        if rollback:
                                            text = ['**容器平台自动上线:**',"项目:%s" %project,"版本:%s" %version,"操作:更新成功", '**请关注业务健康状况!**']
                                        else:
                                            text = ['**容器平台自动回滚:**', "项目:%s" % project, "版本:%s" % version, "操作:回滚成功",
                                                '**请关注业务健康状况!**']
                                else:
                                    if rollback:
                                        _flow_log('%s 镜像回滚失败!' % new_image)
                                        Redis.lpush(redis_key, '%s 镜像回滚失败!' % new_image)
                                        if channel == 'api':
                                            text = ['**容器平台自动回滚:**',"项目:%s" %project,"版本:%s" %version,"操作:回滚失败", '**需要手动处理!**']
                                    else:
                                        deployment.spec.template.spec.containers[0].image = image
                                        if image == new_image:
                                            delete_pod(context,dm_name)
                                        api_instance = client.ExtensionsV1beta1Api()
                                        api_instance.patch_namespaced_deployment(name=dm_name, namespace=namespace,
                                                                                 body=deployment)
                                        _flow_log('%s 镜像更新失败并自动回滚!' % new_image)
                                        Redis.lpush(redis_key,'%s 镜像更新失败并自动回滚!' % new_image)
                                        if channel == 'api':
                                            text = ['**容器平台自动上线:**',"项目:%s" %project,"版本:%s" %version,"操作:失败并回滚", '**需要手动处理!**']
                        Redis.lpush(redis_key, '*'*80)
                    except Exception as e:
                        logging.error(e)
                        _flow_log( 'fail:%s' % e)
                        Redis.lpush(redis_key, 'fail:%s' % e)
                        if channel == 'api':
                            text = ['**容器平台自动上线:**', "项目:%s" % project, "版本:%s" % version, "操作:更新未完成", '**需要手动处理!**']
        except Exception as e:
            logging.error(e)
            if 'BaseException' not in str(e):
                _flow_log('fail:%s' % e)
                Redis.lpush(redis_key, 'fail:%s' % e)
            if channel == 'api':
                text = ['**容器平台自动上线:**', "项目:%s" % project, "版本:%s" % version, "操作:更新未完成", '**需要手动处理!**']
        finally:
            db_op.DB.session.remove()
            Redis.lpush(redis_key, '_End_')
            if channel == 'api':
                tools.dingding_msg(text,ops_token)