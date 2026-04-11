FROM python:3.13

WORKDIR /app

COPY requirements.txt requirements.txt
RUN python -m pip install -r requirements.txt
RUN printf '[project]\nversion = "0.0.0"\n' > /app/pyproject.toml

COPY ./ ./

CMD [ "python3", "main.py" ]
