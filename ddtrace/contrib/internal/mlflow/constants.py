from enum import Enum


MLFLOW_RUN_ID_TAG = "mlflow.run_id"
MLFLOW_PARENT_RUN_ID_TAG = "mlflow.parent_run_id"
MLFLOW_EXPERIMENT_ID_TAG = "mlflow.experiment_id"
MLFLOW_RUN_NAME_TAG = "mlflow.run_name"
LOG_ATTR_MLFLOW_RUN_ID = "dd.mlflow.run_id"
MLFLOW_STEP_TAG = "mlflow.step"


class MLflowLogType(Enum):
    METRICS = "metrics"
    PARAMS = "params"
