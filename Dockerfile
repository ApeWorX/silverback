FROM apeworx/ape:latest

USER root
RUN pip install silverback
RUN chown --recursive harambe:harambe /home/harambe
USER harambe