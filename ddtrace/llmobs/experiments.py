from datetime import datetime
from http.client import HTTPSConnection
import hashlib
import json
import os
from typing import Any, Callable, Dict, List, Union
import sys
import time
from urllib.parse import quote

# Constants
BASE_URL = "api.datadoghq.com"
PROJECT_NAME = "sdk-testing"


class Dataset:
    def __init__(self, name: str, data: List[Dict[str, Any]], description: str = "") -> None:
        self.name = name
        self._validate_data(data)
        self.data = data
        self.description = description

        # Post-push attributes
        self.datadog_dataset_id = None
        

    def __iter__(self) -> iter:
        return iter(self.data)

    def __len__(self) -> int:
        return len(self.data)

    def __getitem__(self, index: int) -> Dict[str, Any]:
        return self.data[index]
        
    
    def __repr__(self) -> str:
        header = f"Dataset: {self.name}\nDescription: {self.description}\nLength: {len(self)}\nDatadog ID: {self.datadog_dataset_id}\n"
        separator = "+" + "-"*10 + "+" + "-"*38 + "+" + "-"*38 + "+"

        def format_dict(d: Dict[str, Any]) -> List[str]:
            def truncate(value: str) -> str:
                return (value[:17] + '...') if len(value) > 20 else value

            return [f"{key}: {truncate(str(value))}" for key, value in d.items()]

        def format_entries(entries):  # Fixed indentation - this was nested too deeply
            formatted_rows = []
            for i, entry in entries:
                input_lines = format_dict(entry['input'])
                expected_output_lines = format_dict(entry.get('expected_output', {}))
                
                # Determine the maximum number of lines in input and expected_output
                max_lines = max(len(input_lines), len(expected_output_lines))
                
                # Pad the lists to have the same number of lines
                input_lines += [''] * (max_lines - len(input_lines))
                expected_output_lines += [''] * (max_lines - len(expected_output_lines))
                
                for j in range(max_lines):
                    if j == 0:
                        index = f"| {i+1:<8} | {input_lines[j]:<38} | {expected_output_lines[j]:<38} |"
                    else:
                        index = f"| {'':<8} | {input_lines[j]:<38} | {expected_output_lines[j]:<38} |"
                    formatted_rows.append(index)
                formatted_rows.append(separator)
            return "\n".join(formatted_rows)

        if len(self.data) <= 4:
            entries = format_entries(enumerate(self.data))
        else:
            first_two = format_entries(enumerate(self.data[:2]))
            last_two = format_entries(enumerate(self.data[-2:], start=len(self.data) - 2))
            entries = f"{first_two}\n| {'...':<8} | {'...':<38} | {'...':<38} |\n{separator}\n{last_two}"

        table = f"{separator}\n| {'Index':<8} | {'Input':<38} | {'Expected Output':<38} |\n{separator}\n{entries}"
        return f"{header}\n{table if entries else 'No entries available.'}\n\n"
    
    def _validate_data(self, data: List[Dict[str, Any]]) -> None:
        if not data:
            raise ValueError("Data cannot be empty.")

        if not all(isinstance(row, dict) for row in data):
            raise ValueError("All rows must be dictionaries.")

        first_row_keys = set(data[0].keys())
        for row in data:
            if set(row.keys()) != first_row_keys:
                raise ValueError("All rows must have the same keys.")
            
            # Check that 'input' and 'expected_output' are flat dictionaries
            for key in ['input', 'expected_output']:
                if key in row and any(isinstance(value, dict) for value in row[key].values()):
                    raise ValueError(f"'{key}' must be a flat dictionary (no nested dictionaries).")

    @classmethod
    def from_datadog(cls, name: str) -> 'Dataset':
        """Create a dataset from a dataset hosted in Datadog.

        Args:
            name: Name of the dataset to retrieve from Datadog

        Returns:
            Dataset: A new Dataset instance populated with the records from Datadog

        Raises:
            ValueError: If the dataset is not found
            Exception: If there are HTTP errors during the request
        """
        conn = HTTPSConnection(BASE_URL)
        headers = {
            "DD-API-KEY": os.getenv("DD_API_KEY"),
            "DD-APPLICATION-KEY": os.getenv("DD_APPLICATION_KEY"),
            "Content-Type": "application/json"
        }

        try:
            # Get dataset ID
            encoded_name = quote(name)
            url = f"/api/unstable/llm-obs/v1/datasets?filter[name]={encoded_name}"
            response_data = _make_request(conn, headers, "GET", url, context="Dataset lookup")
            datasets = response_data.get('data', [])

            if not datasets:
                raise ValueError(f"Dataset '{name}' not found")

            dataset_id = datasets[0]['id']

            # Get dataset records
            url = f"/api/unstable/llm-obs/v1/datasets/{dataset_id}/records"
            records_data = _make_request(conn, headers, "GET", url, context="Records lookup")
            
            # Transform records into the expected format
            class_records = []
            for record in records_data.get('data', []):
                attrs = record.get('attributes', {})
                class_records.append({
                    "input": attrs.get('input', {}),
                    "expected_output": attrs.get('expected_output', {}),
                    **attrs.get('metadata', {})
                })

            # Create new dataset instance
            dataset = cls(name, class_records)
            dataset.datadog_dataset_id = dataset_id
            return dataset

        finally:
            conn.close()

    def push(self) -> Dict[str, str]:
        """Push the dataset to Datadog.
        
        Returns:
            Dict[str, str]: Dictionary containing dataset information including:
                - dataset_id: The ID of the created/updated dataset
                - dataset_name: The name of the dataset
                - record_count: Number of records uploaded
        """
        # Initialize connection and headers
        conn = HTTPSConnection(BASE_URL)
        headers = {
            "DD-API-KEY": os.getenv("DD_API_KEY"),
            "DD-APPLICATION-KEY": os.getenv("DD_APPLICATION_KEY"),
            "Content-Type": "application/json"
        }

        try:
            # Check if dataset exists
            encoded_name = quote(self.name)
            url = f"/api/unstable/llm-obs/v1/datasets?filter[name]={encoded_name}"
            response_data = _make_request(conn, headers, "GET", url, context="Dataset lookup")
            datasets = response_data.get('data', [])

            if not datasets:
                # Create new dataset
                print(f"Dataset '{self.name}' not found. Creating it.")
                dataset_payload = {
                    "data": {
                        "type": "datasets",
                        "attributes": {
                            "name": self.name,
                            "description": self.description or f"Dataset used for {self.name}",
                            "metadata": {"team": "ml-obs"}
                        }
                    }
                }
                response_data = _make_request(
                    conn,
                    headers,
                    "POST",
                    "/api/unstable/llm-obs/v1/datasets",
                    body=json.dumps(dataset_payload),
                    context="Dataset creation"
                )
                dataset_id = response_data['data']['id']
                self.datadog_dataset_id = dataset_id
            else:
                # Dataset exists, create a new version
                dataset_id = datasets[0]['id']
                version_suffix = datetime.now().strftime("%Y-%m-%d-%H-%M-%S")
                new_dataset_name = f"{self.name}-{version_suffix}"
                print(f"Dataset '{self.name}' found. Creating new version '{new_dataset_name}'.")
                dataset_payload = {
                    "data": {
                        "type": "datasets",
                        "attributes": {
                            "name": new_dataset_name,
                            "description": f"Dataset versioned on {version_suffix} used for {self.name}",
                            "metadata": {"team": "ml-obs"}
                        }
                    }
                }
                response_data = _make_request(
                    conn,
                    headers,
                    "POST",
                    "/api/unstable/llm-obs/v1/datasets",
                    body=json.dumps(dataset_payload),
                    context="Dataset version creation"
                )
                dataset_id = response_data['data']['id']
                self.datadog_dataset_id = dataset_id
                self.name = new_dataset_name

            # Add records to the dataset
            records_payload = {
                "data": {
                    "type": "datasets",
                    "attributes": {
                        "records": self.data
                    }
                }
            }
            url = f"/api/unstable/llm-obs/v1/datasets/{dataset_id}/records"
            _make_request(conn, headers, "POST", url, body=json.dumps(records_payload), context="Adding records")

            print(f"✓ Successfully uploaded dataset '{self.name}'")
            print(f"  • Dataset ID: {dataset_id}")
            print(f"  • Records uploaded: {len(self.data)}")
            
            return self

        finally:
            conn.close()




class Experiment:
    def __init__(self, name: str, task: Callable, dataset: Dataset, evaluators: List[Callable]) -> None:
        self.name = name
        self.task = task
        self.dataset = dataset
        self.evaluators = evaluators
        self.tags = []

        # Post-run attributes
        self.has_run = False
        self.results = None


    def __repr__(self) -> str:
        separator = "+" + "-"*20 + "+" + "-"*50 + "+"
        
        def format_evaluator(evaluator: Callable) -> str:
            return f"{evaluator.__name__}"

        evaluator_lines = [format_evaluator(evaluator) for evaluator in self.evaluators]
        evaluators = ", ".join(evaluator_lines) if evaluator_lines else "No evaluators available"

        table = (
            f"{separator}\n"
            f"| {'Experiment':<18} | {self.name:<48} |\n"
            f"{separator}\n"
            f"| {'Task':<18} | {self.task.__name__:<48} |\n"
            f"| {'Dataset':<18} | {f'{self.dataset.name} (n={len(self.dataset)})':<48} |\n"
            f"| {'Evaluators':<18} | {evaluators:<48} |\n"
            f"{separator}"
        )
        return table

    def _validate_tasks(self) -> None:
        # TODO: Design and implement this
        pass

    def _validate_evaluators(self) -> None:
        # TODO: Design and implement this
        pass

    def run(self) -> 'ExperimentResults':
        results = ExperimentResults(self.dataset)
        total_rows = len(self.dataset)

        for idx, row in enumerate(self.dataset, 0):
            # Apply the task function to the row
            start_time = time.time()
            output = self.task(row)
            end_time = time.time()
            duration = end_time - start_time

            # Store the results
            results.experiment_rows.append({
                "output": output,
                "evaluations": [],
                
                "metadata": {
                    "timestamp": start_time,
                    "duration": duration,
                    "dataset_record_idx": idx,
                    "project_name": PROJECT_NAME,
                    "experiment_name": self.name,
                    "dataset_name": self.dataset.name,
                },
                "tags": self.tags,
                "error": None
            })

            def _evaluate_row(row: Dict[str, Any], output: Dict[str, Any]) -> Dict[str, Any]:
                return {evaluator.__name__: evaluator(row, output) for evaluator in self.evaluators}

            results.experiment_rows[idx]["evaluations"] = _evaluate_row(row, output)

            # Update progress
            progress = int(50 * idx / total_rows)  # Progress bar length of 50
            bar = '=' * progress + ' ' * (50 - progress)
            percent = int(100 * idx / total_rows)
            sys.stdout.write(f'\rRunning {self.name}: [{bar}] {percent}% ({idx}/{total_rows})')
            sys.stdout.flush()

        # Print a new line after completion
        sys.stdout.write('\n')

        self.has_run = True
        self.results = results
        return results
    
    def get_results(self) -> 'ExperimentResults':
        if not self.has_run:
            raise ValueError("Experiment has not been run yet")
        return self.results
    
    def push(self) -> Dict[str, str]:
        """Push the experiment results to Datadog.
        
        Returns:
            Dict[str, str]: Dictionary containing experiment information including:
                - experiment_id: The ID of the created experiment
                - experiment_name: The name of the experiment
                - span_count: Number of spans uploaded
        """
        if not self.has_run:
            raise ValueError("Experiment has not been run yet")

        # Initialize connection and headers
        conn = HTTPSConnection(BASE_URL)
        headers = {
            "DD-API-KEY": os.getenv("DD_API_KEY"),
            "DD-APPLICATION-KEY": os.getenv("DD_APPLICATION_KEY"),
            "Content-Type": "application/json"
        }

        try:
            # Check if project exists
            url = f"/api/unstable/llm-obs/v1/projects?filter[name]={PROJECT_NAME}"
            response_data = _make_request(conn, headers, "GET", url, context="Project lookup")
            projects = response_data.get('data', [])

            if not projects:
                # Create new project
                print(f"Project '{PROJECT_NAME}' not found. Creating it.")
                project_payload = {
                    "data": {
                        "type": "projects",
                        "attributes": {
                            "name": PROJECT_NAME,
                            "description": f"Project for {PROJECT_NAME}",
                            "metadata": {"team": "ml-obs"}
                        }
                    }
                }
                response_data = _make_request(
                    conn,
                    headers,
                    "POST",
                    "/api/unstable/llm-obs/v1/projects",
                    body=json.dumps(project_payload),
                    context="Project creation"
                )
                project_id = response_data['data']['id']
            else:
                project_id = projects[0]['id']

            # Check if experiment exists
            encoded_name = quote(self.name)
            url = f"/api/unstable/llm-obs/v1/experiments?filter[name]={encoded_name}"
            response_data = _make_request(conn, headers, "GET", url, context="Experiment lookup")
            experiments = response_data.get('data', [])

            if not experiments:
                # Create new experiment
                print(f"Experiment '{self.name}' not found. Creating it.")
                experiment_payload = {
                    "data": {
                        "type": "experiments",
                        "attributes": {
                            "name": self.name,
                            "description": f"Experiment: {self.name} on dataset: {self.dataset.name}",
                            "dataset_id": self.dataset.datadog_dataset_id,
                            "project_id": project_id,
                            "metadata": {
                                "tags": self.tags,
                                "team": "ml-obs"
                            }
                        }
                    }
                }
                response_data = _make_request(
                    conn,
                    headers,
                    "POST",
                    "/api/unstable/llm-obs/v1/experiments",
                    body=json.dumps(experiment_payload),
                    context="Experiment creation"
                )
                experiment_id = response_data['data']['id']
            else:
                # Experiment exists, create a new version
                version_suffix = datetime.now().strftime("%Y-%m-%d-%H-%M-%S")
                new_experiment_name = f"{self.name}-{version_suffix}"
                print(f"Experiment '{self.name}' found. Creating new version '{new_experiment_name}'.")
                experiment_payload = {
                    "data": {
                        "type": "experiments",
                        "attributes": {
                            "name": new_experiment_name,
                            "description": f"Experiment versioned on {version_suffix} used for {self.name}",
                            "dataset_id": self.dataset.datadog_dataset_id,
                            "project_id": project_id,
                            "metadata": {
                                "tags": self.tags,
                                "team": "ml-obs"
                            }
                        }
                    }
                }
                response_data = _make_request(
                    conn,
                    headers,
                    "POST",
                    "/api/unstable/llm-obs/v1/experiments",
                    body=json.dumps(experiment_payload),
                    context="Experiment version creation"
                )
                experiment_id = response_data['data']['id']
                self.name = new_experiment_name

            # Prepare and send experiment results
            spans = []
            metrics = []

            
            
            for idx, result in enumerate(self.results):
                
                span = {
                    "span_id": _make_id(),
                    "project_id": project_id,
                    "experiment_id": experiment_id,
                    "dataset_id": self.dataset.datadog_dataset_id,
                    "dataset_record_id": _make_id(),
                    "start_ns": int(result['metadata']['timestamp'] * 1e9),
                    "duration": float(result['metadata']['duration'] * 1e9),
                    "tags": self.tags,
                    "status": "ok",
                    "meta": {
                        "span": {"kind": "experiment"},
                        "input": self.dataset[idx]['input'],
                        "output": result['output'],
                        "expected_output": self.dataset[idx].get('expected_output', {}),
                        "error": {
                           "message": result['error'],
                           "stack": None,
                           "type": None 
                        }
                    }
                    
                }
                spans.append(span)

                # Add evaluation metrics
                for metric_name, metric_value in result['evaluations'].items():
                    timestamp_ms = int(result['metadata']['timestamp'] * 1000)
                    
                    if isinstance(metric_value, bool):
                        metric_value = 1 if metric_value else 0
                        metric_type = "score" 
                    elif isinstance(metric_value, (int, float)):
                        metric_type = "score"
                    else:
                        metric_type = "categorical"
                        metric_value = str(metric_value)

                    metric = {
                        "span_id": span['span_id'],
                        "metric_type": metric_type,
                        "timestamp_ms": timestamp_ms,
                        "label": metric_name,
                        "score_value" if metric_type == "score" else "categorical_value": metric_value
                    }
                    metrics.append(metric)

            results_payload = {
                "data": {
                    "type": "experiments",
                    "attributes": {
                        "spans": spans,
                        "metrics": metrics
                    }
                }
            }


            url = f"/api/unstable/llm-obs/v1/experiments/{experiment_id}/events"
            _make_request(
                conn,
                headers,
                "POST",
                url,
                body=json.dumps(results_payload),
                context="Publishing results"
            )

            print(f"✓ Successfully uploaded experiment '{self.name}'")
            print(f"  • Experiment ID: {experiment_id}")
            print(f"  • Spans uploaded: {len(spans)}")
            print(f"  • Metrics uploaded: {len(metrics)}")
            
            return self

        finally:
            conn.close()




class ExperimentResults:
    def __init__(self, dataset: Dataset) -> None:
        self.dataset = dataset
        self.experiment_rows = []

    def __repr__(self) -> str:
        separator = "+" + "-"*10 + "+" + "-"*38 + "+" + "-"*38 + "+" + "-"*38 + "+" + "-"*38 + "+"

        def format_dict(d: Union[Dict[str, Any], List[Any]]) -> List[str]:
            if isinstance(d, dict):
                def truncate(value: str) -> str:
                    return (value[:17] + '...') if len(value) > 20 else value

                return [f"{key}: {truncate(str(value))}" for key, value in d.items()]
            elif isinstance(d, list):
                return [str(item) for item in d]
            else:
                return [str(d)]

        def format_entries(entries):
            formatted_rows = []
            for i, entry in enumerate(entries):
                dataset_entry = self.dataset[i]
                input_lines = format_dict(dataset_entry['input'])
                expected_output_lines = format_dict(dataset_entry.get('expected_output', {}))
                output_lines = format_dict(entry['output'])
                evaluations_lines = format_dict(entry.get('evaluations', []))
                
                # Determine the maximum number of lines across all fields
                max_lines = max(len(input_lines), len(expected_output_lines), len(output_lines), len(evaluations_lines))
                
                # Pad the lists to have the same number of lines
                input_lines += [''] * (max_lines - len(input_lines))
                expected_output_lines += [''] * (max_lines - len(expected_output_lines))
                output_lines += [''] * (max_lines - len(output_lines))
                evaluations_lines += [''] * (max_lines - len(evaluations_lines))
                
                for j in range(max_lines):
                    if j == 0:
                        index = f"| {i+1:<8} | {input_lines[j]:<38} | {expected_output_lines[j]:<38} | {output_lines[j]:<38} | {evaluations_lines[j]:<38} |"
                    else:
                        index = f"| {'':<8} | {input_lines[j]:<38} | {expected_output_lines[j]:<38} | {output_lines[j]:<38} | {evaluations_lines[j]:<38} |"
                    formatted_rows.append(index)
                formatted_rows.append(separator)
            return "\n".join(formatted_rows)

        if len(self.experiment_rows) <= 4:
            entries = format_entries(self.experiment_rows)
        else:
            first_two = format_entries(self.experiment_rows[:2])
            last_two = format_entries(self.experiment_rows[-2:])
            entries = f"{first_two}\n| {'...':<8} | {'...':<38} | {'...':<38} | {'...':<38} | {'...':<38} |\n{separator}\n{last_two}"

        table = (
            f"{separator}\n"
            f"| {'Index':<8} | {'Input':<38} | {'Expected Output':<38} | {'Output':<38} | {'Evaluations':<38} |\n"
            f"{separator}\n"
            f"{entries}"
        )
        return f"Experiment Results:\n{table if entries else 'No results available.'}\n\n"

    def __iter__(self) -> iter:
        return iter(self.experiment_rows)
    
    def __len__(self) -> int:
        return len(self.experiment_rows)

    def __getitem__(self, index: int) -> Any:
        return self.experiment_rows[index]



def _make_request(conn: HTTPSConnection, headers: Dict[str, Any], method: str, url: str, body: Any = None, context: str = "") -> Dict[str, Any]:
    if method == "GET":
        conn.request(method, url, headers=headers)
    else:
        if body is not None and isinstance(body, str):
            body = body.encode('utf-8')
        conn.request(method, url, body=body, headers=headers)
    
    response = conn.getresponse()
    response_body = response.read()
    
    if response.status >= 400:
        error_message = f"HTTP {response.status} Error during {context}: {response.reason}\nResponse body: {response_body.decode('utf-8')}"
        raise Exception(error_message)
    
    # Add handling for empty response
    if not response_body:
        return {}  # Return empty dict for empty responses
        
    try:
        return json.loads(response_body)
    except json.JSONDecodeError:
        error_message = f"Invalid JSON response during {context}. Status: {response.status}\nResponse body: {response_body.decode('utf-8')}"
        raise Exception(error_message)

def _make_id() -> str:
    return hashlib.sha256(datetime.now().isoformat().encode('utf-8')).hexdigest()





