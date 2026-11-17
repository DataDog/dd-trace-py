#!/usr/bin/env python3
"""
Format benchmark results from JSON into a human-readable markdown report.

This script processes benchmark performance test results, compares them against
baseline values and SLOs (Service Level Objectives), and generates a formatted
markdown report with status indicators and performance metrics.

Usage:
    format-benchmark-results.py [fail-on-breach.json]

Environment Variables:
    REPORTS_DIR: Directory containing the fail-on-breach.json file and baseline-*.converted.json files
"""

import sys
import json
import os
import re
from collections import defaultdict
from dataclasses import dataclass
from typing import Optional, Dict, List, Tuple, Any


# Configuration constants
class Config:
    """Configuration constants for the benchmark formatter."""


    # Threshold percentages for different status indicators
    THRESHOLDS = {
        'regression': 10,       # >10% worse than baseline triggers regression indicator
        'improvement': -10,     # >10% better than baseline triggers improvement indicator
        'near_breach': -2,      # Within 2% of SLO triggers warning
        'near_breach_max': 0,   # Upper bound for near-breach warning
    }

    # Display width constants for alignment
    DISPLAY_WIDTHS = {
        'value': 9,         # Width for formatted values (e.g., "999.999ms")
        'percentage': 7,    # Width for percentages (e.g., "+999.9%")
        'slo': 11,         # Width for SLO values (e.g., "<999.999ms")
    }

    # Unicode characters for formatting
    UNICODE_CHARS = {
        'fig_space': '\u2007',  # Figure space for number alignment
        'em_space': '\u2003',   # Em space for wider spacing
    }

    # Status icons
    STATUS_ICONS = {
        'pass': '✅',
        'warning': '⚠️',
        'fail': '❌',
        'regression': '📈',
        'improvement': '📉',
        'near_breach': '🟡',
        'no_baseline': '🟡',
    }


@dataclass
class Metric:
    """Data class representing a single performance metric."""
    name: str
    value: Optional[float]
    unit: str
    slo: Optional[float]
    slo_display_value: Optional[float]
    slo_unit: str
    status: str
    icon: str
    scenario: str
    scenario_full_name: str
    conclusion: str
    unstable: bool

    @property
    def is_pass(self) -> bool:
        """Check if metric passed."""
        return self.status == "pass"

    @property
    def is_unstable(self) -> bool:
        """Check if metric is unstable."""
        return self.status == "unstable"

    @property
    def is_fail(self) -> bool:
        """Check if metric failed."""
        return self.status not in ["pass", "unstable"]


class UnitConverter:
    """Handles unit conversions for time and memory measurements."""

    # Time unit conversions to nanoseconds
    TIME_UNITS = {
        "ns": 1,
        "µs": 1000,
        "us": 1000,
        "ms": 1000000,
        "s": 1000000000
    }

    # Memory unit conversions to bytes
    MEMORY_UNITS = {
        "B": 1,
        "KB": 1024,
        "MB": 1024 * 1024,
        "GB": 1024 * 1024 * 1024
    }

    @classmethod
    def convert(cls, value: float, from_unit: str, to_unit: str) -> float:
        """
        Convert a value between units.

        Args:
            value: The numeric value to convert
            from_unit: Source unit
            to_unit: Target unit

        Returns:
            Converted value, or original value if conversion not possible
        """
        if not value or not from_unit or not to_unit:
            return value

        # Check if time units
        if from_unit in cls.TIME_UNITS and to_unit in cls.TIME_UNITS:
            # Convert to nanoseconds then to target
            value_ns = value * cls.TIME_UNITS[from_unit]
            return value_ns / cls.TIME_UNITS[to_unit]

        # Check if memory units
        if from_unit in cls.MEMORY_UNITS and to_unit in cls.MEMORY_UNITS:
            # Convert to bytes then to target
            value_bytes = value * cls.MEMORY_UNITS[from_unit]
            return value_bytes / cls.MEMORY_UNITS[to_unit]

        return value


class Formatter:
    """Handles all formatting operations for values, percentages, and alignment."""

    @staticmethod
    def right_align(text: str, width: int, padding_char: str = None) -> str:
        """
        Right-align text to specified width using padding character.

        Args:
            text: Text to align
            width: Target width
            padding_char: Character to use for padding (default: figure space)

        Returns:
            Right-aligned text
        """
        if padding_char is None:
            padding_char = Config.UNICODE_CHARS['fig_space']

        current_width = len(text)
        if current_width < width:
            padding = padding_char * (width - current_width)
            return padding + text
        return text

    @staticmethod
    def format_value_with_unit(value: float, unit: str) -> str:
        """
        Format a numeric value with its unit, applying appropriate precision.

        Args:
            value: Numeric value to format
            unit: Unit string (e.g., "ms", "MB")

        Returns:
            Formatted string with right-alignment
        """
        if unit in ["ns", "µs", "us"]:
            formatted = f"{value:.3f}{unit}"
        elif unit == "ms":
            if value < 0.1:
                # Convert to microseconds for better readability
                formatted = f"{value * 1000:.3f}µs"
            else:
                formatted = f"{value:.3f}{unit}"
        elif unit == "s":
            if value < 0.001:
                # Convert to milliseconds
                formatted = f"{value * 1000:.3f}ms"
            else:
                formatted = f"{value:.3f}{unit}"
        elif unit == "MB":
            formatted = f"{value:.3f}{unit}"
        else:
            formatted = f"{value:.3f}{unit}"

        return formatted

    @staticmethod
    def format_percentage(value: Optional[float], show_emoji: bool = True,
                         override_emoji: Optional[str] = None) -> str:
        """
        Format a percentage value with optional emoji indicators.

        Args:
            value: Percentage value to format
            show_emoji: Whether to show emoji indicators
            override_emoji: Specific emoji to use (overrides automatic selection)

        Returns:
            Formatted percentage string with alignment and optional emoji
        """
        # Format the percentage
        if value is None:
            formatted = "N/A"
        elif abs(value) < 0.1:
            formatted = "~same"
        elif value > 0:
            formatted = f"+{value:.1f}%"
        else:
            formatted = f"{value:.1f}%"

        # Determine emoji to use
        emoji = ""
        if override_emoji:
            emoji = override_emoji
        elif show_emoji and value is not None:
            if value > Config.THRESHOLDS['regression']:
                emoji = Config.STATUS_ICONS['regression']
            elif value < Config.THRESHOLDS['improvement']:
                emoji = Config.STATUS_ICONS['improvement']

        # Build final result
        if emoji:
            result = emoji + " " + formatted
        else:
            result = formatted

        return result

    @staticmethod
    def format_slo(value: float, unit: str) -> str:
        """
        Format SLO value with '<' prefix and right-alignment.

        Args:
            value: SLO threshold value
            unit: Unit for the SLO

        Returns:
            Formatted SLO string
        """
        formatted_value = Formatter.format_value_with_unit(value, unit).strip()
        slo_str = f"<{formatted_value}"
        return slo_str


class BaselineDataLoader:
    """Handles loading and processing of baseline data from artifacts."""

    @staticmethod
    def load(reports_dir: Optional[str]) -> Tuple[Dict[str, Dict[str, float]], Dict[str, Any]]:
        """
        Load baseline data from baseline converted JSON files in reports directory.

        Args:
            reports_dir: Path to reports directory containing baseline-*.converted.json files

        Returns:
            Tuple of (baseline_data, baseline_metadata) where:
            - baseline_data: Dictionary mapping scenario names to their baseline metrics
            - baseline_metadata: Dictionary containing git commit info
        """
        baseline_data = {}
        baseline_metadata = {}

        if not reports_dir or not os.path.exists(reports_dir):
            return baseline_data, baseline_metadata

        # Load metadata from baseline.converted.json
        baseline_converted_path = os.path.join(reports_dir, "baseline.converted.json")
        if os.path.exists(baseline_converted_path):
            baseline_metadata = BaselineDataLoader._load_baseline_metadata(baseline_converted_path)

        # Load baseline data from all baseline-*.converted.json files
        baseline_data.update(BaselineDataLoader._load_baseline_converted_files(reports_dir))

        return baseline_data, baseline_metadata



    @staticmethod
    def _load_baseline_metadata(json_path: str) -> Dict[str, Any]:
        """Load baseline metadata from converted JSON file."""
        metadata = {}

        try:
            with open(json_path, 'r') as f:
                data = json.load(f)

            # Extract git metadata from the first benchmark entry
            benchmarks = data.get("benchmarks", [])
            if benchmarks and len(benchmarks) > 0:
                first_entry = benchmarks[0]
                if "parameters" in first_entry:
                    params = first_entry["parameters"]
                    metadata["git_commit_sha"] = params.get("git_commit_sha", "")
                    metadata["git_commit_date"] = params.get("git_commit_date", "")
                    metadata["git_branch"] = params.get("git_branch", "")

        except Exception:
            # Skip if we can't read the file
            pass

        return metadata

    @staticmethod
    def get_baseline_entry(baseline_data: Dict[str, Dict[str, float]],
                          scenario_full_name: str,
                          scenario_name: str) -> Optional[Dict[str, float]]:
        """
        Get baseline entry for a scenario, checking multiple name formats.

        Args:
            baseline_data: The baseline data dictionary
            scenario_full_name: Full scenario name (e.g., "suite-scenario")
            scenario_name: Short scenario name

        Returns:
            Baseline metrics dictionary or None if not found
        """
        if scenario_full_name in baseline_data:
            return baseline_data[scenario_full_name]
        elif scenario_name in baseline_data:
            return baseline_data[scenario_name]
        return None

    @staticmethod
    def _load_baseline_converted_files(reports_dir: str) -> Dict[str, Dict[str, float]]:
        """
        Load baseline data from all baseline-*.converted.json files in reports directory.

        Args:
            reports_dir: Path to the reports directory containing baseline converted JSON files

        Returns:
            Dictionary mapping scenario names to their baseline metrics
        """
        baseline_data = {}

        if not os.path.exists(reports_dir):
            return baseline_data

        # Find all baseline-*.converted.json files
        for filename in os.listdir(reports_dir):
            if filename.startswith("baseline-") and filename.endswith(".converted.json"):
                file_path = os.path.join(reports_dir, filename)
                try:
                    suite_data = BaselineDataLoader._load_baseline_converted_file(file_path)
                    baseline_data.update(suite_data)
                except Exception:
                    # Skip files that can't be processed
                    continue

        return baseline_data

    @staticmethod
    def _load_baseline_converted_file(file_path: str) -> Dict[str, Dict[str, float]]:
        """Load baseline data from a single baseline converted JSON file."""
        suite_data = {}

        try:
            with open(file_path, 'r') as f:
                data = json.load(f)

            for benchmark in data.get("benchmarks", []):
                # Get scenario name from parameters instead of metadata
                scenario_name = benchmark["parameters"]["scenario"]
                metrics = {}

                # Process execution time values from runs object
                runs = benchmark.get("runs", {})
                time_values = []
                memory_values = []

                for run_key, run_data in runs.items():
                    if isinstance(run_data, dict):
                        # Extract execution time values
                        if "execution_time" in run_data and "values" in run_data["execution_time"]:
                            time_values.extend(run_data["execution_time"]["values"])

                        # Extract memory values
                        if "max_rss_usage" in run_data and "values" in run_data["max_rss_usage"]:
                            memory_values.extend(run_data["max_rss_usage"]["values"])

                if time_values:
                    # Calculate average execution time and convert to microseconds
                    avg_time = sum(time_values) / len(time_values)
                    metrics["execution_time"] = avg_time * 1_000_000  # Convert from seconds to microseconds

                if memory_values:
                    # Calculate average memory usage and convert to MB
                    avg_memory = sum(memory_values) / len(memory_values)
                    metrics["max_rss_usage"] = avg_memory / (1024 * 1024)  # Convert from bytes to MB

                suite_data[scenario_name] = metrics

        except (json.JSONDecodeError, KeyError) as e:
            pass  # Skip files with parsing errors

        return suite_data


class MetricAnalyzer:
    """Analyzes metrics for status determination and comparisons."""

    @staticmethod
    def calculate_percentage_change(current: float, baseline: float) -> Optional[float]:
        """
        Calculate percentage change from baseline.

        Args:
            current: Current value
            baseline: Baseline value to compare against

        Returns:
            Percentage change or None if calculation not possible
        """
        if not current or not baseline or baseline <= 0:
            return None
        return ((current - baseline) / baseline) * 100

    @staticmethod
    def calculate_slo_percentage(value: float, slo: float) -> Optional[float]:
        """
        Calculate how far a value is from its SLO as a percentage.

        Args:
            value: Current value
            slo: SLO threshold

        Returns:
            Percentage difference from SLO (negative means under SLO)
        """
        if not value or not slo or slo <= 0:
            return None
        return ((value - slo) / slo) * 100

    @staticmethod
    def is_near_breach(slo_percentage: Optional[float]) -> bool:
        """Check if a metric is near SLO breach."""
        if slo_percentage is None:
            return False
        return (Config.THRESHOLDS['near_breach'] <= slo_percentage <
                Config.THRESHOLDS['near_breach_max'])

    @staticmethod
    def analyze_suite_metrics(metrics: List[Metric],
                            baseline_data: Dict[str, Dict[str, float]]) -> Dict[str, bool]:
        """
        Analyze a suite's metrics for various conditions.

        Args:
            metrics: List of metrics to analyze
            baseline_data: Baseline data for comparison

        Returns:
            Dictionary with analysis results
        """
        analysis = {
            'has_failures': False,
            'has_regressions': False,
            'has_warnings': False,
            'has_improvements': False,
            'has_unstable': False,
            'has_no_baseline': True,  # Assume no baseline until we find one
        }

        for metric in metrics:
            # Check for failures
            if metric.is_fail:
                analysis['has_failures'] = True

            # Check for unstable
            if metric.is_unstable:
                analysis['has_unstable'] = True

            # Check baseline comparison
            baseline_entry = BaselineDataLoader.get_baseline_entry(
                baseline_data, metric.scenario_full_name, metric.scenario
            )

            if baseline_entry and metric.name in baseline_entry:
                # We found baseline data, so set flag to False
                analysis['has_no_baseline'] = False

                baseline_value = baseline_entry[metric.name]

                # Convert baseline value if needed
                if metric.name == "execution_time":
                    # Baseline is stored in microseconds, convert to metric's unit
                    baseline_converted = UnitConverter.convert(baseline_value, "µs", metric.unit)
                else:
                    baseline_converted = baseline_value

                change_pct = MetricAnalyzer.calculate_percentage_change(
                    metric.value, baseline_converted
                )

                if change_pct is not None:
                    if change_pct > Config.THRESHOLDS['regression']:
                        analysis['has_regressions'] = True
                    elif change_pct < Config.THRESHOLDS['improvement']:
                        analysis['has_improvements'] = True

            # Check for near-breach warnings
            slo_pct = MetricAnalyzer.calculate_slo_percentage(metric.value, metric.slo)
            if MetricAnalyzer.is_near_breach(slo_pct):
                analysis['has_warnings'] = True

        return analysis


class ReportGenerator:
    """Generates the formatted benchmark report."""

    def __init__(self, data: Dict[str, Any], baseline_data: Dict[str, Dict[str, float]],
                 baseline_metadata: Optional[Dict[str, Any]] = None, reports_dir: Optional[str] = None):
        """
        Initialize the report generator.

        Args:
            data: Benchmark results data
            baseline_data: Baseline data for comparison
            baseline_metadata: Optional baseline git metadata
            reports_dir: Optional reports directory path for detecting missing SLO scenarios
        """
        self.data = data
        self.baseline_data = baseline_data
        self.baseline_metadata = baseline_metadata or {}
        self.metadata = data.get("metadata", {})
        self.reports_dir = reports_dir

    def generate(self) -> str:
        """
        Generate the complete benchmark report.

        Returns:
            Formatted markdown report
        """
        output = []

        # Add header
        output.extend(self._generate_header())

        # Add performance results
        output.extend(self._generate_performance_results())

        # Add missing SLO scenarios section
        missing_slo_section = self._generate_missing_slo_section()
        if missing_slo_section:
            output.extend(missing_slo_section)

        return "\n".join(output)

    def _generate_header(self) -> List[str]:
        """Generate report header with comparison information."""
        header = []

        # Try to add comparison info from file
        comparison_info = self._load_comparison_info()
        if comparison_info:
            header.append(comparison_info)
            header.append("")
        else:
            # Build comparison info from metadata
            candidate_info = ""
            baseline_info = ""

            # Candidate info
            if self.metadata:
                candidate_branch = self.metadata.get("candidate_branch", self.metadata.get("git_branch", "unknown"))
                candidate_sha = self.metadata.get("candidate_commit", self.metadata.get("git_commit_sha", "unknown"))[:8]
                candidate_info = f"{candidate_branch} ({candidate_sha})"

            # Baseline info
            if self.baseline_metadata:
                baseline_branch = self.baseline_metadata.get("git_branch", "unknown")
                baseline_sha = self.baseline_metadata.get("git_commit_sha", "unknown")[:8]
                baseline_info = f"{baseline_branch} ({baseline_sha})"

            if candidate_info and baseline_info:
                header.append(f"Comparing candidate {candidate_info} with baseline {baseline_info}")
                header.append("")
            elif candidate_info:
                header.append(f"Candidate: {candidate_info}")
                header.append("")

        return header

    def _load_comparison_info(self) -> Optional[str]:
        """Load comparison information from comparison file if available."""
        reports_dir = os.environ.get("REPORTS_DIR", "")
        if not reports_dir:
            return None

        comparison_file = os.path.join(reports_dir, "comparison-baseline-vs-candidate.md")

        if not os.path.exists(comparison_file):
            return None

        try:
            with open(comparison_file, 'r') as f:
                content = f.read()
                match = re.search(
                    r'candidate commit (\w+) in PR branch `([^`]+)` with baseline commit (\w+) in branch `([^`]+)`',
                    content
                )
                if match:
                    return (f"Comparing candidate {match.group(2)} ({match.group(1)}) "
                           f"with baseline {match.group(4)} ({match.group(3)})")
        except Exception:
            pass

        return None

    def _generate_performance_results(self) -> List[str]:
        """Generate the main performance results section."""
        output = []

        # Process test suites
        test_suites = self._process_scenarios()

        # Group suites by status
        status_groups = self._group_suites_by_status(test_suites)

        # Generate output for each status group
        status_order = [
            ('fail', '❌ Test Failures'),
            ('regression', '📈 Performance Regressions'),
            ('near_breach', '🟡 Near SLO Breach'),
            ('no_baseline', '🔵 No Baseline Data'),
            ('improvement', '📉 Performance Improvements'),
            ('warning', '⚠️ Unstable Tests'),
            ('pass', '✅ All Tests Passing')
        ]

        for status_key, status_title in status_order:
            if status_key in status_groups and status_groups[status_key]:
                group_output = self._generate_status_group(status_title, status_groups[status_key])
                output.extend(group_output)

        return output

    def _process_scenarios(self) -> Dict[str, Dict[str, List[Metric]]]:
        """Process scenarios into organized test suites."""
        test_suites = defaultdict(lambda: {"scenarios": defaultdict(list)})

        for scenario_full_name, metrics_dict in self.data.get("scenarios", {}).items():
            # Parse suite and scenario from the name
            suite, _, scenario = scenario_full_name.partition("-")

            # Process each metric type
            for metric_type, metrics_list in metrics_dict.items():
                for metric_data in metrics_list:
                    metric = self._create_metric(metric_data, scenario, scenario_full_name)
                    test_suites[suite]["scenarios"][scenario].append(metric)

        return test_suites

    def _group_suites_by_status(self, test_suites: Dict[str, Dict[str, Any]]) -> Dict[str, List[Tuple[str, Dict[str, Any]]]]:
        """Group suites by their status."""
        status_groups = defaultdict(list)

        for suite_name, suite_data in test_suites.items():
            # Get all metrics for the suite
            all_metrics = []
            for scenario_name, metrics in suite_data["scenarios"].items():
                all_metrics.extend(metrics)

            if not all_metrics:
                continue

            # Analyze suite status
            analysis = MetricAnalyzer.analyze_suite_metrics(all_metrics, self.baseline_data)
            status_icon = self._determine_suite_status_icon(analysis)

            # Categorize by status
            if analysis['has_failures']:
                status_groups['fail'].append((suite_name, suite_data))
            elif analysis['has_regressions']:
                status_groups['regression'].append((suite_name, suite_data))
            elif analysis.get('has_no_baseline', False):
                status_groups['no_baseline'].append((suite_name, suite_data))
            elif analysis['has_warnings']:
                status_groups['near_breach'].append((suite_name, suite_data))
            elif analysis['has_improvements']:
                status_groups['improvement'].append((suite_name, suite_data))
            elif analysis['has_unstable']:
                status_groups['warning'].append((suite_name, suite_data))
            else:
                status_groups['pass'].append((suite_name, suite_data))

        return status_groups

    def _generate_status_group(self, status_title: str, suites: List[Tuple[str, Dict[str, Any]]]) -> List[str]:
        """Generate output for a status group."""
        output = []

        # Create the group header
        suite_count = len(suites)
        output.append("<details>")
        output.append(f"<summary><strong>{status_title} ({suite_count} suite{'s' if suite_count != 1 else ''})</strong></summary>")
        output.append("")

        # Generate each suite in the group
        for i, (suite_name, suite_data) in enumerate(sorted(suites)):
            if i > 0:  # Add separator between suites (but not before the first one)
                output.append("---")
                output.append("")
            suite_output = self._generate_suite_output(suite_name, suite_data)
            output.extend(suite_output)

        output.append("</details>")
        output.append("")

        return output

    def _create_metric(self, metric_data: Dict[str, Any],
                      scenario: str, scenario_full_name: str) -> Metric:
        """Create a Metric object from raw metric data."""
        conclusion = metric_data.get("conclusion", "fail")
        unstable = metric_data.get("unstable", False)

        # Determine status and icon
        if conclusion == "pass" and not unstable:
            status = "pass"
            icon = Config.STATUS_ICONS['pass']
        elif unstable:
            status = "unstable"
            icon = Config.STATUS_ICONS['warning']
        else:
            status = "fail"
            icon = Config.STATUS_ICONS['fail']

        # Extract SLO display values
        threshold = metric_data.get("breach_threshold")
        threshold_str = metric_data.get("threshold_str", "")
        slo_unit = metric_data.get("uom", "")
        slo_display_value = threshold

        if threshold_str and threshold:
            # Extract unit and value from threshold string for display
            threshold_match = re.search(r'([\d.]+)([^\d\s]+)$', threshold_str)
            if threshold_match:
                slo_display_value = float(threshold_match.group(1))
                slo_unit = threshold_match.group(2)

        return Metric(
            name=metric_data.get("metric_name", "unknown"),
            value=metric_data.get("measurement"),
            unit=metric_data.get("uom", ""),
            slo=threshold,
            slo_display_value=slo_display_value,
            slo_unit=slo_unit,
            status=status,
            icon=icon,
            scenario=scenario,
            scenario_full_name=scenario_full_name,
            conclusion=conclusion,
            unstable=unstable
        )

    def _generate_suite_output(self, suite_name: str,
                             suite_data: Dict[str, Any]) -> List[str]:
        """Generate output for a single test suite."""
        output = []

        all_metrics = []
        for scenario_name, metrics in suite_data["scenarios"].items():
            all_metrics.extend(metrics)

        if not all_metrics:
            return output

        # Calculate suite statistics
        stats = self._calculate_suite_stats(all_metrics)

        # Analyze metrics for status determination
        analysis = MetricAnalyzer.analyze_suite_metrics(all_metrics, self.baseline_data)

        # Determine suite status icon
        status_icon = self._determine_suite_status_icon(analysis)

        # Create summary line
        summary = self._create_suite_summary(suite_name, stats, status_icon)

        output.append("<details>")
        output.append(f"<summary>{summary}</summary>")
        output.append("")

        # Check if suite has baseline data
        suite_has_baseline = not analysis.get('has_no_baseline', False)

        if not suite_has_baseline:
            output.append("🔵 **No baseline data available for this suite**")
            output.append("")

        # Generate scenario tables
        for i, (scenario_name, metrics) in enumerate(sorted(suite_data["scenarios"].items())):
            if metrics:
                if i > 0:  # Add separator between scenarios (but not before the first one)
                    output.append("---")
                    output.append("")
                scenario_output = self._generate_scenario_output(scenario_name, metrics, suite_has_baseline)
                output.extend(scenario_output)

        output.append("</details>")
        output.append("")

        return output

    def _calculate_suite_stats(self, metrics: List[Metric]) -> Dict[str, int]:
        """Calculate statistics for a suite."""
        return {
            'pass': sum(1 for m in metrics if m.is_pass),
            'unstable': sum(1 for m in metrics if m.is_unstable),
            'fail': sum(1 for m in metrics if m.is_fail),
            'total': len(metrics),
            'passed': sum(1 for m in metrics if m.is_pass or m.is_unstable)
        }

    def _determine_suite_status_icon(self, analysis: Dict[str, bool]) -> str:
        """
        Determine the appropriate status icon for a suite.

        Priority order:
        1. ❌ when there are any failures
        2. 📈 if there are any regressions
        3. 🔵 if there is no baseline data
        4. 🟡 if there are any "warnings"
        5. 📉 if there are any improvements
        6. ⚠️ if there are any unstable builds
        7. ✅ otherwise show that all is green/stable
        """
        if analysis['has_failures']:
            return Config.STATUS_ICONS['fail']
        elif analysis['has_regressions']:
            return Config.STATUS_ICONS['regression']
        elif analysis.get('has_no_baseline', False):
            return '🔵'
        elif analysis['has_warnings']:
            return Config.STATUS_ICONS['near_breach']
        elif analysis['has_improvements']:
            return Config.STATUS_ICONS['improvement']
        elif analysis['has_unstable']:
            return Config.STATUS_ICONS['warning']
        else:
            return Config.STATUS_ICONS['pass']

    def _create_suite_summary(self, suite_name: str, stats: Dict[str, int],
                            status_icon: str) -> str:
        """Create suite summary line."""
        if stats['unstable'] > 0:
            pass_info = f"{stats['passed']}/{stats['total']} ({stats['unstable']} unstable)"
        else:
            pass_info = f"{stats['passed']}/{stats['total']}"

        return f"{status_icon} <strong>{suite_name}</strong> - {pass_info}"

    def _generate_scenario_output(self, scenario_name: str,
                                metrics: List[Metric],
                                suite_has_baseline: bool = True) -> List[str]:
        """Generate output for a single scenario."""
        output = []

        # Determine scenario status
        scenario_icon = self._determine_scenario_icon(metrics)

        # Create compact output
        output.append(f"#### {scenario_icon} {scenario_name}")

        # Group metrics by type
        time_metric = next((m for m in metrics if m.name == "execution_time"), None)
        memory_metric = next((m for m in metrics if m.name == "max_rss_usage"), None)

        if time_metric:
            time_str = self._format_compact_metric(time_metric, suite_has_baseline)
            output.append(f"Time: {time_str}")
            output.append("")  # Add blank line for markdown rendering

        if memory_metric:
            mem_str = self._format_compact_metric(memory_metric, suite_has_baseline)
            output.append(f"Memory: {mem_str}")

        output.append("")

        return output

    def _format_compact_metric(self, metric: Metric, has_baseline: bool) -> str:
        """Format a metric in compact form."""
        parts = []

        # Status icon
        parts.append(metric.icon)

        # Candidate value
        candidate_str = self._format_candidate_value(metric)
        parts.append(candidate_str.strip())

        # SLO comparison
        slo_str = self._format_slo_value(metric)
        slo_percentage = MetricAnalyzer.calculate_slo_percentage(metric.value, metric.slo)
        vs_slo_str = self._format_vs_slo(slo_percentage)

        parts.append(f"(SLO: {slo_str.strip()} {vs_slo_str.strip()})")

        # Baseline comparison if available
        if has_baseline:
            baseline_value_str, baseline_change_str, baseline_change_pct = self._format_baseline_comparison(metric)
            if baseline_value_str.strip():  # Only show if we have baseline data
                parts.append(f"vs baseline: {baseline_change_str.strip()}")

        return " ".join(parts)

    def _scenario_has_baseline(self, metrics: List[Metric]) -> bool:
        """Check if any metric in the scenario has baseline data."""
        for metric in metrics:
            baseline_entry = BaselineDataLoader.get_baseline_entry(
                self.baseline_data, metric.scenario_full_name, metric.scenario
            )
            if baseline_entry and metric.name in baseline_entry:
                return True
        return False

    def _determine_scenario_icon(self, metrics: List[Metric]) -> str:
        """Determine the appropriate icon for a scenario."""
        has_fail = any(m.is_fail for m in metrics)
        has_unstable = any(m.is_unstable for m in metrics)

        if has_fail:
            return Config.STATUS_ICONS['fail']
        elif has_unstable:
            return Config.STATUS_ICONS['warning']
        else:
            return Config.STATUS_ICONS['pass']

    def _generate_metric_row(self, metric: Metric, has_baseline: bool = True) -> str:
        """Generate a table row for a single metric."""
        # Format candidate value
        candidate_str = self._format_candidate_value(metric)

        # Format SLO
        slo_str = self._format_slo_value(metric)

        # Calculate vs SLO
        slo_percentage = MetricAnalyzer.calculate_slo_percentage(metric.value, metric.slo)
        vs_slo_str = self._format_vs_slo(slo_percentage)

        if has_baseline:
            # Format baseline value and comparison
            baseline_value_str, baseline_change_str, baseline_change_pct = self._format_baseline_comparison(metric)

            # Determine final icon (may be modified based on combined risk)
            icon = self._determine_metric_icon(metric, slo_percentage, baseline_change_pct)

            return f"| {metric.name} | {icon} | {candidate_str} | {baseline_value_str} | {slo_str} | {vs_slo_str} | {baseline_change_str} |"
        else:
            # No baseline columns
            icon = metric.icon
            return f"| {metric.name} | {icon} | {candidate_str} | {slo_str} | {vs_slo_str} |"

    def _format_candidate_value(self, metric: Metric) -> str:
        """Format the candidate value for display."""
        if not metric.value:
            return "N/A"

        if metric.slo_unit and metric.slo_unit != metric.unit:
            # Convert candidate value to SLO display unit
            converted_value = UnitConverter.convert(metric.value, metric.unit, metric.slo_unit)
            return Formatter.format_value_with_unit(converted_value, metric.slo_unit)
        else:
            return Formatter.format_value_with_unit(metric.value, metric.unit)

    def _format_baseline_comparison(self, metric: Metric) -> Tuple[str, str, Optional[float]]:
        """Format baseline value and comparison percentage."""
        baseline_entry = BaselineDataLoader.get_baseline_entry(
            self.baseline_data, metric.scenario_full_name, metric.scenario
        )

        if not baseline_entry or metric.name not in baseline_entry:
            return (
                "",  # Empty baseline value
                "",  # Empty vs baseline
                None
            )

        baseline_value = baseline_entry[metric.name]
        if not baseline_value:
            return (
                "N/A",
                Formatter.format_percentage(None),
                None
            )

        # Convert baseline value
        if metric.name == "execution_time":
            # Baseline is stored in microseconds, convert to metric's unit
            baseline_converted = UnitConverter.convert(baseline_value, "µs", metric.unit)
        else:
            baseline_converted = baseline_value

        # Format baseline value
        if baseline_converted and metric.slo_unit and metric.slo_unit != metric.unit:
            baseline_display = UnitConverter.convert(baseline_converted, metric.unit, metric.slo_unit)
            baseline_value_str = Formatter.format_value_with_unit(baseline_display, metric.slo_unit)
        elif baseline_converted:
            baseline_value_str = Formatter.format_value_with_unit(baseline_converted, metric.unit)
        else:
            baseline_value_str = "N/A"

        # Calculate percentage change
        baseline_change_pct = MetricAnalyzer.calculate_percentage_change(
            metric.value, baseline_converted
        )
        baseline_change_str = Formatter.format_percentage(baseline_change_pct, show_emoji=True)

        return baseline_value_str, baseline_change_str, baseline_change_pct

    def _format_slo_value(self, metric: Metric) -> str:
        """Format the SLO value for display."""
        if metric.slo_display_value:
            return Formatter.format_slo(metric.slo_display_value, metric.slo_unit)
        else:
            return "N/A"

    def _format_vs_slo(self, slo_percentage: Optional[float]) -> str:
        """Format the vs SLO percentage with appropriate indicators."""
        if slo_percentage is None:
            return Formatter.format_percentage(None)

        # Check for near-breach warning
        if MetricAnalyzer.is_near_breach(slo_percentage):
            return Formatter.format_percentage(
                slo_percentage,
                show_emoji=True,
                override_emoji=Config.STATUS_ICONS['near_breach']
            )
        else:
            return Formatter.format_percentage(slo_percentage, show_emoji=True)

    def _determine_metric_icon(self, metric: Metric,
                             slo_percentage: Optional[float],
                             baseline_change_pct: Optional[float]) -> str:
        """Determine the final icon for a metric based on all factors."""
        # Rule: Combined risk - use warning when <2% SLO buffer AND >10% baseline regression
        if (slo_percentage is not None and
            MetricAnalyzer.is_near_breach(slo_percentage) and
            baseline_change_pct is not None and
            baseline_change_pct > Config.THRESHOLDS['regression']):
            return Config.STATUS_ICONS['warning']

        return metric.icon

    def _generate_missing_slo_section(self) -> List[str]:
        """Generate section for scenarios without SLO configuration."""
        if not self.reports_dir or not os.path.exists(self.reports_dir):
            return []

        try:
            # Get all scenarios from candidate converted files
            candidate_scenarios = set()
            for filename in os.listdir(self.reports_dir):
                if filename.startswith('candidate-') and filename.endswith('.converted.json'):
                    filepath = os.path.join(self.reports_dir, filename)
                    try:
                        with open(filepath, 'r') as f:
                            data = json.load(f)
                        for benchmark in data.get('benchmarks', []):
                            scenario = benchmark['parameters']['scenario']
                            candidate_scenarios.add(scenario)
                    except Exception:
                        continue

            # Get scenarios from current data (these have SLO checks)
            slo_scenarios = set(self.data.get('scenarios', {}).keys())

            # Find missing
            missing_slo = candidate_scenarios - slo_scenarios

            if not missing_slo:
                return []

            output = []
            output.append("")
            output.append("<details>")
            output.append(f"<summary><strong>ℹ️ Scenarios Missing SLO Configuration ({len(missing_slo)} scenarios)</strong></summary>")
            output.append("")
            output.append("The following scenarios exist in candidate data but have no SLO thresholds configured:")
            output.append("")

            for scenario in sorted(missing_slo):
                output.append(f"- `{scenario}`")

            output.append("")
            output.append("</details>")

            return output

        except Exception:
            # If we can't detect missing scenarios, don't break the report
            return []


def load_json_data(json_file: str) -> Dict[str, Any]:
    """
    Load and validate JSON data from file.

    Args:
        json_file: Path to JSON file

    Returns:
        Parsed JSON data

    Raises:
        FileNotFoundError: If file doesn't exist
        ValueError: If JSON is invalid
    """
    if not os.path.exists(json_file):
        raise FileNotFoundError(f"JSON file not found: {json_file}")

    try:
        with open(json_file, "r") as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON in {json_file}: {e}")


def format_results(json_file: str, reports_dir: Optional[str] = None) -> str:
    """
    Main entry point for formatting benchmark results.

    Args:
        json_file: Path to the fail-on-breach.json file
        reports_dir: Optional path to reports directory for baseline comparison

    Returns:
        Formatted markdown report
    """
    # Load data
    data = load_json_data(json_file)

    # Load baseline data and metadata
    if reports_dir:
        baseline_data, baseline_metadata = BaselineDataLoader.load(reports_dir)
    else:
        baseline_data, baseline_metadata = {}, {}

    # Generate report
    generator = ReportGenerator(data, baseline_data, baseline_metadata, reports_dir)
    return generator.generate()


def main():
    """Main entry point for the script."""
    # Check for environment variables first
    reports_dir = os.environ.get("REPORTS_DIR", "")

    # Handle command line arguments
    if len(sys.argv) > 1:
        json_file = sys.argv[1]
    elif reports_dir:
        # Use fail-on-breach.json from REPORTS_DIR
        json_file = os.path.join(reports_dir, "fail-on-breach.json")
    else:
        print("Usage: format-benchmark-results.py [fail-on-breach.json]")
        print("Or set REPORTS_DIR environment variable")
        sys.exit(1)

    try:
        result = format_results(json_file, reports_dir)
        print(result)
    except FileNotFoundError as e:
        print(f"Error: {e}")
        sys.exit(1)
    except ValueError as e:
        print(f"Error: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"Unexpected error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()