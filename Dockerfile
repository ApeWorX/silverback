FROM apeworx/ape:latest

USER root
RUN pip install silverback
USER harambe

ENTRYPOINT ["silverback"]
