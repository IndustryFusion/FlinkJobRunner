# alerts_shacl.py
from __future__ import annotations
from dataclasses import dataclass
from typing import List, Tuple, Optional, Dict, Any
import json
import re

from flask import Blueprint, request, jsonify

# Optional deps (PySHACL/RDFlib). Endpoints handle missing libs gracefully.
try:
    from rdflib import Graph
    from pyshacl import validate as shacl_validate
    PYSHACL_AVAILABLE = True
except Exception:
    PYSHACL_AVAILABLE = False

bp = Blueprint("alerts_shacl", __name__, url_prefix="/api")

# ---------- Domain models ----------
@dataclass
class AlertCondition:
    sensor: str
    operator: str
    threshold: float
    unit: Optional[str] = None

# ---------- Parser ----------
class AlertParser:
    """Parse natural language alert requests"""

    SENSOR_KEYWORDS = {
        'temperature': ['temp', 'temperature', 'degrees'],
        'humidity': ['humidity', 'moisture'],
        'noise': ['noise', 'sound', 'decibel', 'db'],
        'dustiness': ['dust', 'dustiness', 'particulate', 'pm'],
        'machine_state': ['state', 'status', 'machine']
    }

    OPERATOR_PATTERNS: List[Tuple[str, str]] = [
        (r'(?:above|greater than|exceeds?|over|more than)\s+(-?\d+\.?\d*)', '>'),
        (r'(?:below|less than|under)\s+(-?\d+\.?\d*)', '<'),
        (r'(?:at least|minimum|min)\s+(-?\d+\.?\d*)', '>='),
        (r'(?:at most|maximum|max)\s+(-?\d+\.?\d*)', '<='),
        (r'(?:equals?|is|==)\s+(-?\d+\.?\d*)', '=='),
    ]

    UNIT_KEYWORDS = {
        'temperature': ['c', '°c', 'celsius', 'f', '°f', 'fahrenheit'],
        'humidity': ['%', 'percent', 'percentage', 'rh'],
        'noise': ['db', 'decibel'],
        'dustiness': ['µg/m3', 'ug/m3', 'mg/m3', 'ppm'],
    }

    def parse(self, prompt: str) -> AlertCondition:
        p = prompt.strip().lower()
        sensor = self._extract_sensor(p)
        operator, threshold = self._extract_condition(p)
        unit = self._extract_unit(p, sensor)
        return AlertCondition(sensor=sensor, operator=operator, threshold=threshold, unit=unit)

    def _extract_sensor(self, text: str) -> str:
        for sensor, kws in self.SENSOR_KEYWORDS.items():
            for kw in kws:
                if re.search(rf'\b{re.escape(kw)}\b', text):
                    return sensor
        # fallback
        return 'temperature'

    def _extract_condition(self, text: str) -> Tuple[str, float]:
        for pat, op in self.OPERATOR_PATTERNS:
            m = re.search(pat, text)
            if m:
                return op, float(m.group(1))
        # numeric fallback: first number as threshold with '>'
        m = re.search(r'(-?\d+\.?\d*)', text)
        if m:
            return '>', float(m.group(1))
        return '>', 0.0

    def _extract_unit(self, text: str, sensor: str) -> Optional[str]:
        for u in self.UNIT_KEYWORDS.get(sensor, []):
            if re.search(rf'\b{re.escape(u)}\b', text):
                return u
        return None

# ---------- SHACL Validator ----------
class SHACLValidator:
    """Validate generated SHACL rules using PySHACL"""

    @staticmethod
    def generate_template_shacl(jsonld_data: dict, condition: AlertCondition) -> str:
        """Generate correct NGSI-LD SHACL as fallback when LLM fails"""
        asset_type = (jsonld_data.get('type', '') or '').split('/')[-1] or 'Asset'

        constraint_map = {
            '>': ('sh:maxInclusive', condition.threshold),
            '<': ('sh:minInclusive', condition.threshold),
            '>=': ('sh:maxExclusive', condition.threshold),
            '<=': ('sh:minExclusive', condition.threshold),
            '==': ('sh:hasValue', condition.threshold)
        }
        shacl_op, threshold = constraint_map.get(condition.operator, ('sh:maxInclusive', condition.threshold))

        return f"""@prefix sh: <http://www.w3.org/ns/shacl#> .
@prefix if: <https://industry-fusion.org/base/v0.1/> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
@prefix ngsi-ld: <https://uri.etsi.org/ngsi-ld/> .

if:{asset_type.capitalize()}AlertShape a sh:NodeShape ;
  sh:targetClass if:{asset_type} ;
  sh:property [
    sh:path if:{condition.sensor} ;
    sh:property [
      sh:path ngsi-ld:hasValue ;
      {shacl_op} "{threshold}"^^xsd:decimal ;
      sh:severity sh:Violation ;
      sh:message "{condition.sensor.capitalize()} {condition.operator} {threshold}{condition.unit or ''}" ;
    ] ;
  ] .
"""

    @staticmethod
    def create_test_scenarios(jsonld_data: dict, condition: AlertCondition) -> List[dict]:
        """Create test scenarios: one that should trigger, one that shouldn't"""
        scenarios = []
        # Scenario 1: Should TRIGGER
        trigger_value = condition.threshold + 10 if condition.operator in ['>', '>='] else condition.threshold - 10
        scenarios.append({
            "name": f"Should TRIGGER ({condition.sensor} {condition.operator} {condition.threshold})",
            "jsonld": SHACLValidator._modify_sensor_value(jsonld_data, condition.sensor, trigger_value),
            "should_trigger": True
        })
        # Scenario 2: Should NOT trigger
        safe_value = condition.threshold - 10 if condition.operator in ['>', '>='] else condition.threshold + 10
        scenarios.append({
            "name": f"Should NOT trigger ({condition.sensor} safe)",
            "jsonld": SHACLValidator._modify_sensor_value(jsonld_data, condition.sensor, safe_value),
            "should_trigger": False
        })
        return scenarios

    @staticmethod
    def _modify_sensor_value(jsonld_data: dict, sensor: str, value: float) -> dict:
        """Create a copy of JSON-LD with modified sensor value"""
        data = json.loads(json.dumps(jsonld_data))  # Deep copy
        sensor_key = f"https://industry-fusion.org/base/v0.1/{sensor}"
        if sensor_key in data:
            # Keep as number for proper RDF typing
            if isinstance(data[sensor_key], dict):
                data[sensor_key]["value"] = value
            else:
                data[sensor_key] = {"value": value}
        else:
            data[sensor_key] = {"value": value}
        return data

    @staticmethod
    def validate_shacl_rule(shacl_rule: str, test_scenarios: List[dict]) -> dict:
        """Test SHACL rule against scenarios"""
        if not PYSHACL_AVAILABLE:
            return {"available": False, "message": "PySHACL not installed"}

        results = []
        all_passed = True

        for scenario in test_scenarios:
            data_graph = Graph()
            data_graph.parse(data=json.dumps(scenario["jsonld"]), format='json-ld')

            shacl_graph = Graph()
            try:
                shacl_graph.parse(data=shacl_rule, format='turtle')
            except Exception as e:
                return {
                    "available": True,
                    "valid_syntax": False,
                    "error": f"SHACL syntax error: {e}"
                }

            try:
                conforms, _, _ = shacl_validate(
                    data_graph,
                    shacl_graph=shacl_graph,
                    inference='rdfs',
                    abort_on_first=False
                )

                triggered = not conforms
                expected = scenario["should_trigger"]
                passed = (triggered == expected)

                results.append({
                    "scenario": scenario["name"],
                    "expected": "TRIGGER" if expected else "NO TRIGGER",
                    "actual": "TRIGGERED" if triggered else "OK",
                    "passed": passed
                })

                if not passed:
                    all_passed = False

            except Exception as e:
                results.append({
                    "scenario": scenario["name"],
                    "error": str(e)
                })
                all_passed = False

        return {
            "available": True,
            "valid_syntax": True,
            "all_tests_passed": all_passed,
            "test_results": results
        }

# ---------- Flask endpoints ----------

@bp.post("/alerts/parse")
def parse_alert():
    """
    Body: { "prompt": "Alert me if temperature exceeds 75 C" }
    """
    data = request.get_json(force=True, silent=True) or {}
    prompt = data.get("prompt", "")
    parser = AlertParser()
    cond = parser.parse(prompt)
    return jsonify({
        "sensor": cond.sensor,
        "operator": cond.operator,
        "threshold": cond.threshold,
        "unit": cond.unit
    })

@bp.post("/shacl/generate")
def generate_shacl():
    """
    Body: {
      "prompt": "...",           # optional; if present, parse to condition
      "jsonld_data": {...},      # required
      "condition": { "sensor": "...", "operator": ">", "threshold": 10, "unit": "°C" }  # optional
    }
    """
    body = request.get_json(force=True, silent=True) or {}
    jsonld_data = body.get("jsonld_data") or {}
    cond = _condition_from_body_or_prompt(body)
    shacl = SHACLValidator.generate_template_shacl(jsonld_data, cond)
    return jsonify({"shacl": shacl})

@bp.post("/shacl/validate")
def validate_shacl():
    """
    Body: {
      "shacl_rule": "@prefix sh: ...",
      "jsonld_data": {...},
      "prompt": "...",     # optional, to derive condition & scenarios
      "condition": {...}   # optional, overrides prompt
    }
    """
    if not PYSHACL_AVAILABLE:
        return jsonify({
            "available": False,
            "message": "PySHACL not installed. Install with: pip install pyshacl rdflib"
        }), 200

    body = request.get_json(force=True, silent=True) or {}
    shacl_rule = body.get("shacl_rule") or ""
    jsonld_data = body.get("jsonld_data") or {}
    cond = _condition_from_body_or_prompt(body)

    # Build test scenarios
    test_scenarios = SHACLValidator.create_test_scenarios(jsonld_data, cond)

    # Validate
    result = SHACLValidator.validate_shacl_rule(shacl_rule, test_scenarios)
    # Mirror your CLI-style summary in response
    summary = {
        "available": result.get("available", False),
        "valid_syntax": result.get("valid_syntax", False),
        "all_tests_passed": result.get("all_tests_passed", False),
        "test_results": result.get("test_results", []),
    }
    if not result.get("available", True):
        summary["hint"] = "pip install pyshacl rdflib"
    if not result.get("valid_syntax", True):
        summary["error"] = result.get("error")
    return jsonify(summary)

@bp.post("/alerts/validate-cli-style")
def validate_cli_style():
    """
    (Optional) Returns the CLI-like text block your snippet printed.
    Body: {
      "args": { "prompt": "...", "validate": true },
      "jsonld_data": {...},
      "shacl_rule": "..."
    }
    """
    body = request.get_json(force=True, silent=True) or {}
    args = (body.get("args") or {})
    prompt = args.get("prompt", "")
    validate_flag = bool(args.get("validate", True))
    shacl_rule = body.get("shacl_rule") or ""
    jsonld_data = body.get("jsonld_data") or {}

    parser = AlertParser()
    condition = parser.parse(prompt)

    lines = []
    if not validate_flag:
        return jsonify({"output": ""})

    if not PYSHACL_AVAILABLE:
        lines.append("⚠️  PySHACL not available. Install with: pip install pyshacl rdflib")
        return jsonify({"output": "\n".join(lines)})

    lines += [
        "\n" + "="*60,
        "PySHACL Validation Test",
        "="*60
    ]

    test_scenarios = SHACLValidator.create_test_scenarios(jsonld_data, condition)
    validation_result = SHACLValidator.validate_shacl_rule(shacl_rule, test_scenarios)

    if not validation_result.get("valid_syntax"):
        lines.append(f"❌ SHACL Syntax Error: {validation_result.get('error')}")
    else:
        for result in validation_result["test_results"]:
            if "error" in result:
                lines.append(f"\n❌ {result['scenario']}: ERROR - {result['error']}")
            else:
                status = "✅ PASS" if result["passed"] else "❌ FAIL"
                lines.append(f"\n{status} {result['scenario']}")
                lines.append(f"  Expected: {result['expected']}")
                lines.append(f"  Actual: {result['actual']}")

        lines.append("\n" + "="*60)
        if validation_result.get("all_tests_passed"):
            lines.append("✅ ALL VALIDATION TESTS PASSED")
            lines.append("Your Ollama model generated correct SHACL!")
        else:
            lines.append("❌ VALIDATION TESTS FAILED")
        lines.append("="*60)

    return jsonify({"output": "\n".join(lines)})

# ---------- helpers ----------
def _condition_from_body_or_prompt(body: Dict[str, Any]) -> AlertCondition:
    if body.get("condition"):
        c = body["condition"]
        return AlertCondition(
            sensor=c.get("sensor", "temperature"),
            operator=c.get("operator", ">"),
            threshold=float(c.get("threshold", 0)),
            unit=c.get("unit")
        )
    prompt = body.get("prompt", "")
    return AlertParser().parse(prompt or "temperature above 0")

