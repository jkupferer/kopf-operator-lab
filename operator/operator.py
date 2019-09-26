import kopf
import kubernetes
import os

operator_domain = os.environ.get('OPERATOR_DOMAIN', 'example.redhat.com')
operator_version = os.environ.get('OPERATOR_VERSION', 'v1')

if os.path.exists('/run/secrets/kubernetes.io/serviceaccount/token'):
    f = open('/run/secrets/kubernetes.io/serviceaccount/token')
    kube_auth_token = f.read()
    kube_config = kubernetes.client.Configuration()
    kube_config.api_key['authorization'] = kube_auth_token
    kube_config.api_key_prefix['authorization'] = 'Bearer'
    kube_config.host = os.environ['KUBERNETES_PORT'].replace('tcp://', 'https://', 1)
    kube_config.ssl_ca_cert = '/run/secrets/kubernetes.io/serviceaccount/ca.crt'
else:
    kubernetes.config.load_kube_config()
    kube_config = None

core_v1_api = kubernetes.client.CoreV1Api(
    kubernetes.client.ApiClient(kube_config)
)
custom_objects_api = kubernetes.client.CustomObjectsApi(
    kubernetes.client.ApiClient(kube_config)
)

def get_user_quota_config(name, logger):
    try:
        return custom_objects_api.get_cluster_custom_object(
            operator_domain, operator_version, 'userquotaconfigs', name
        )
    except kubernetes.client.rest.ApiException as e:
        if e.status == 404:
            logger.warning('UserQuotaConfig %s not found', name)
        else:
            raise

@kopf.on.create(operator_domain, operator_version, 'quotarequests')
def handle_quota_request_create(body, logger, **_):
    handle_quota_request_reconcile(body, logger)

@kopf.on.update(operator_domain, operator_version, 'quotarequests')
def handle_quota_request_update(body, logger, **_):
    handle_quota_request_reconcile(body, logger)

@kopf.on.delete(operator_domain, operator_version, 'quotarequests')
def handle_quota_request_delete(body, logger, **_):
    name = quota_request['metadata']['name']
    namespace = quota_request['metadata']['namespace']
    pass

def handle_quota_request_reconcile(quota_request, logger):
    name = quota_request['metadata']['name']
    namespace = quota_request['metadata']['namespace']
    try:
        quota = core_v1_api.read_namespaced_resource_quota(name, namespace)
        if quota.spec.hard != quota_request['spec']['hard']:
            quota.spec.hard = quota_request['spec']['hard']
            core_v1_api.replace_namespaced_resource_quota(name, namespace, quota)
            logger.info('updated ResourceQuota')
    except kubernetes.client.rest.ApiException as e:
        if e.status == 404:
            core_v1_api.create_namespaced_resource_quota(
                namespace,
                kubernetes.client.V1ResourceQuota(
                    metadata = kubernetes.client.V1ObjectMeta(
                        name = name,
                        namespace = namespace
                    ),
                    spec = kubernetes.client.V1ResourceQuotaSpec(
                        hard = quota_request['spec']['hard']
                    )
                )
            )
            logger.info('created ResourceQuota')
        else:
            raise

@kopf.on.event('user.openshift.io', 'v1', 'users')
def handle_user_event(event, logger, **_):
    user = event['object']
    if event['type'] == 'DELETE':
        handle_user_delete(user, logger)
    elif event['type'] in ['ADDED', 'MODIFIED', None]:
        handle_user_quota(user, logger)
    else:
        logger.warning('Unhandled user event %s', event)

def handle_user_delete(user, logger):
    user_name = user['metadata']['name']
    try:
        custom_objects_api.delete_cluster_custom_object(
            'quota.openshift.io', 'v1', user_name
        )
    except kubernetes.client.rest.ApiException as e:
        if e.status != 404:
            raise

def handle_user_quota(user, logger):
    user_name = user['metadata']['name']
    try:
        user_quota = custom_objects_api.get_cluster_custom_object(
            'quota.openshift.io', 'v1', 'clusterresourcequotas', 'user-' + user_name
        )
        handle_user_quota_update(user_quota, user, logger)
    except kubernetes.client.rest.ApiException as e:
        if e.status == 404:
            handle_user_quota_create(user, logger)
        else:
            raise

def handle_user_quota_create(user, logger):
    user_name = user['metadata']['name']

    user_quota_config = get_user_quota_config('default', logger)
    if not user_quota_config:
        return

    custom_objects_api.create_cluster_custom_object(
        'quota.openshift.io', 'v1', 'clusterresourcequotas',
        {
            'apiVersion': 'quota.openshift.io/v1',
            'kind': 'ClusterResourceQuota',
            'metadata': {
                'name': 'user-' + user_name,
                'annotations': {
                    operator_domain + '/user-quota-config': 'default'
                }
            },
            'spec': {
                'quota': user_quota_config['spec']['quota'],
                'selector': {
                    'annotations': {
                        'openshift.io/requester': user_name
                    }
                }
            }
        }
    )

def handle_user_quota_update(user_quota, user, logger):
    user_quota_name = 'user-' + user_quota['metadata']['name']
    user_quota_config_name = user_quota['metadata'] \
        .get('annotations', {}) \
        .get(operator_domain + '/user-quota-config', 'default')
    user_quota_config = get_user_quota_config(user_quota_config_name, logger)

    if not user_quota_config:
        user_quota_config = get_user_quota_config('default', logger)

    if not user_quota_config:
        return

    if user_quota_config['spec']['quota'] == user_quota['spec']['quota']:
        return

    user_quota['spec']['quota'] = user_quota_config['spec']['quota']
    custom_objects_api.replace_cluster_custom_object(
        'quota.openshift.io', 'v1', 'clusterresourcequotas', user_quota_name, user_quota
    )
