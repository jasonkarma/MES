"""OpenAI function calling schemas for structured data extraction."""

# ISO 14224 failure mode taxonomy (simplified for prototype)
ISO_FAILURE_MODES = [
    "Tooling Wear",
    "Mechanical Failure",
    "Electrical Fault",
    "Process Deviation",
    "Material Defect",
    "Calibration Error",
    "Software Error"
]

ISO_SYMPTOMS = [
    "Vibration",
    "Surface Defect",
    "Dimensional Error",
    "Assembly Error",
    "Noise",
    "Leakage",
    "Overheating",
    "Blockage"
]

ISO_SEVERITY_LEVELS = ["Low", "Medium", "High", "Critical"]

# ISO 14224 code mapping (simplified taxonomy)
ISO_14224_CODES = {
    "TW-001": "Cutting tool degradation",
    "TW-002": "Tool holder failure",
    "MF-001": "Bearing failure",
    "MF-002": "Shaft misalignment",
    "MF-003": "Fastener loosening",
    "EF-001": "Sensor malfunction",
    "EF-002": "Motor failure",
    "PD-001": "Feed rate deviation",
    "PD-002": "Temperature out of spec",
    "PD-003": "Pressure variation",
    "PD-004": "Cycle time overrun",
    "PD-005": "Queue blockage",
    "MD-001": "Material contamination",
    "MD-002": "Material hardness variation",
    "CE-001": "Measurement instrument drift",
    "SE-001": "PLC program error"
}

# OpenAI function calling schema for defect classification
DEFECT_CLASSIFICATION_FUNCTION = {
    "name": "classify_defect",
    "description": "Extract structured failure data from operator defect report using ISO 14224 taxonomy",
    "parameters": {
        "type": "object",
        "properties": {
            "failure_mode": {
                "type": "string",
                "enum": ISO_FAILURE_MODES,
                "description": "Primary failure category according to ISO 14224"
            },
            "iso_code": {
                "type": "string",
                "description": f"Specific ISO 14224 failure code. Must be one of: {', '.join(ISO_14224_CODES.keys())}"
            },
            "symptom": {
                "type": "string",
                "enum": ISO_SYMPTOMS,
                "description": "Observable symptom reported or inferred"
            },
            "component": {
                "type": "string",
                "description": "Specific part, tool, or equipment affected (e.g., 'Tool Insert T3', 'Spindle Bearing', 'Collet')"
            },
            "location": {
                "type": "string",
                "description": "Station, step number, or physical location (e.g., 'Step 2', 'Station 02', 'Assembly Line 1')"
            },
            "severity": {
                "type": "string",
                "enum": ISO_SEVERITY_LEVELS,
                "description": "Impact level: Low (minor, production continues), Medium (reduced capacity), High (production stopped), Critical (safety risk)"
            },
            "confidence": {
                "type": "number",
                "minimum": 0.0,
                "maximum": 1.0,
                "description": "Confidence in this classification (0.0 = very uncertain, 1.0 = very certain). Use < 0.6 if information is ambiguous or missing."
            },
            "reasoning": {
                "type": "string",
                "description": "Brief explanation of the classification decision and any assumptions made"
            }
        },
        "required": ["failure_mode", "iso_code", "symptom", "severity", "confidence", "reasoning"]
    }
}

# OpenAI function calling schema for action generation
ACTION_GENERATION_FUNCTION = {
    "name": "generate_diagnostic_checklist",
    "description": "Generate a 3-step diagnostic checklist and maintenance recommendation",
    "parameters": {
        "type": "object",
        "properties": {
            "checklist_steps": {
                "type": "array",
                "items": {
                    "type": "string"
                },
                "minItems": 3,
                "maxItems": 3,
                "description": "Exactly 3 diagnostic steps in order of execution. Each step must be specific, actionable, and measurable."
            },
            "severity_score": {
                "type": "integer",
                "minimum": 0,
                "maximum": 100,
                "description": "Numerical severity for prioritization. 0-30: Low, 31-60: Medium, 61-85: High, 86-100: Critical"
            },
            "estimated_downtime_minutes": {
                "type": "integer",
                "minimum": 0,
                "description": "Estimated time in minutes to diagnose and resolve this issue"
            },
            "requires_maintenance_ticket": {
                "type": "boolean",
                "description": "True if this requires a maintenance ticket to be created immediately"
            },
            "maintenance_priority": {
                "type": "string",
                "enum": ["low", "normal", "high", "urgent"],
                "description": "Maintenance ticket priority level if ticket is required"
            },
            "safety_notes": {
                "type": "string",
                "description": "Any safety considerations or warnings for the operator (empty string if none)"
            }
        },
        "required": ["checklist_steps", "severity_score", "estimated_downtime_minutes", 
                     "requires_maintenance_ticket", "maintenance_priority"]
    }
}

# Function to validate ISO code against taxonomy
def validate_iso_code(iso_code: str) -> bool:
    """
    Validate that ISO code exists in taxonomy.
    
    Args:
        iso_code: ISO 14224 code to validate
    
    Returns:
        True if valid, False otherwise
    """
    return iso_code in ISO_14224_CODES

def get_iso_description(iso_code: str) -> str:
    """
    Get human-readable description for ISO code.
    
    Args:
        iso_code: ISO 14224 code
    
    Returns:
        Description or "Unknown code" if not found
    """
    return ISO_14224_CODES.get(iso_code, "Unknown code")
