FROM joyzoursky/python-chromedriver

COPY gamevote.py /
COPY dynamic-nomad-313003-66bddaa48fb0.json /
COPY requirements.txt /
COPY config.ini /

RUN pip install --no-cache-dir -r /requirements.txt

CMD ["python", "/gamevote.py"]