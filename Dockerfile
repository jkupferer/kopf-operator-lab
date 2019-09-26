FROM registry.access.redhat.com/ubi8:latest

USER 0

COPY operator /operator

RUN yum install -y nss_wrapper && \
    /operator/python3-install.sh && \
    /usr/local/bin/pip3 install kopf kubernetes && \
    yum clean all && \
    rm -rf /var/cache/yum && \
    mkdir -m a=rwx /operator/nss

USER 1000

CMD /operator/run.sh
