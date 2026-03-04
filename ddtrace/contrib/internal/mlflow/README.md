## Using Datadog's Authentication Plugin for MLFlow

1. Set the `DD_API_KEY` and `DD_APP_KEY` environment variables:

   ```bash
   export DD_API_KEY="your_api_key_here"
   export DD_APP_KEY="your_app_key_here"
   export DD_MODEL_LAB_ENABLED="true"
   ```

2. You will now be able to submit authenticated requests to Datadog's MLFlow tracking server:

   ```python
   import mlflow

   mlflow.set_tracking_uri("https://app.dataddoghq.com/api/v1/mlflow-tracking-server"")
   ```

   MLFlow will automatically recognize and use the plugin as long as the ddtrace Python library is installed.
