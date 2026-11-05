FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PORT=8080
WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
RUN python -m pip install --no-cache-dir . \
    && groupadd --gid 10001 app \
    && useradd --uid 10001 --gid app --no-create-home app
COPY examples/rubrics.json ./examples/rubrics.json
USER 10001:10001
EXPOSE 8080
CMD ["python", "-m", "foundry_decision_eval.main"]
