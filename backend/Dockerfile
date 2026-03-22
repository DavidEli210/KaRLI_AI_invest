FROM public.ecr.aws/lambda/python:3.12

COPY requirements.txt ${LAMBDA_TASK_ROOT}
RUN sed -i 's/\r$//' requirements.txt && pip install -r requirements.txt

COPY . ${LAMBDA_TASK_ROOT}

CMD [ "main.handler" ]