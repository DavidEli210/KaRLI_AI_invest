FROM python:3.13

WORKDIR /app

COPY requirements.txt requirements.txt
RUN python -m pip install -r requirements.txt

RUN pip install uv
ENV PATH="/root/.local/bin:$PATH"

COPY ./ ./

CMD [ "python3", "main.py" ]
