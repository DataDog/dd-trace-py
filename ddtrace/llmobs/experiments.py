from ddtrace import config
from typing import List, Dict, Any, Callable, Union
import time
import sys

class Dataset:
    def __init__(self, name: str, data: List[Dict[str, Any]], description: str = "") -> None:
        self.name = name
        self.data = data
        self.description = description
        self._validate_data()

    def __iter__(self) -> iter:
        return iter(self.data)

    def __len__(self) -> int:
        return len(self.data)

    def __getitem__(self, index: int) -> Dict[str, Any]:
        return self.data[index]
    
    def __repr__(self) -> str:
        header = f"Dataset: {self.name}\nDescription: {self.description}\nLength: {len(self)}\n"
        separator = "+" + "-"*10 + "+" + "-"*38 + "+" + "-"*38 + "+"

        def format_dict(d: Dict[str, Any]) -> List[str]:
            def truncate(value: str) -> str:
                return (value[:17] + '...') if len(value) > 20 else value

            return [f"{key}: {truncate(str(value))}" for key, value in d.items()]

        def format_entries(entries):
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
    
    def _validate_data(self) -> None:
        if not self.data:
            raise ValueError("Data cannot be empty.")

        if not all(isinstance(row, dict) for row in self.data):
            raise ValueError("All rows must be dictionaries.")

        first_row_keys = set(self.data[0].keys())
        for row in self.data:
            if set(row.keys()) != first_row_keys:
                raise ValueError("All rows must have the same keys.")
            
            # Check that 'input' and 'expected_output' are flat dictionaries
            for key in ['input', 'expected_output']:
                if key in row and any(isinstance(value, dict) for value in row[key].values()):
                    raise ValueError(f"'{key}' must be a flat dictionary (no nested dictionaries).")

    @classmethod
    def from_datadog(cls, name: str) -> 'Dataset':
        # TODO: Implement this
        pass

    def push(self) -> None:
        # TODO: Implement this
        print(config._dd_api_key)
        pass


class Experiment:
    def __init__(self, name: str, task: Callable, dataset: Dataset, evaluators: List[Callable]) -> None:
        self.name = name
        self.task = task
        self.dataset = dataset
        self.evaluators = evaluators

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
        # TODO: Implement this
        pass

    def _validate_evaluators(self) -> None:
        # TODO: Implement this
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
                    "duration": duration,
                    "timestamp": start_time
                }
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

        return results


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

    def push(self) -> None:
        # TODO: Implement this
        pass
