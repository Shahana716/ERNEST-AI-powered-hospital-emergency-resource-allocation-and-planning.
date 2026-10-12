import pathlib
from typing import Literal, Optional, List, Dict, Any
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, model_validator
from ai_engine.core import Problem, search, build_plan
from hospital import hospital_instance, HospitalManager

app = FastAPI(title="Hospital Emergency Resource Planner & Capacity Manager")

class Patient(BaseModel):
    id: str = Field(min_length=1)
    severity: Literal["critical", "serious", "moderate"]
    waiting_time: int = Field(0, ge=0)
    unit: Literal["icu", "emergency", "general"]
    ventilator: bool = False
    surgery: bool = False

class Resources(BaseModel):
    icu_beds: int = Field(ge=0); emergency_beds: int = Field(ge=0); general_beds: int = Field(ge=0)
    ventilators: int = Field(ge=0); operating_rooms: int = Field(ge=0)
    doctors: int = Field(ge=0); nurses: int = Field(ge=0)

class PlanRequest(BaseModel):
    patients: list[Patient] = Field(min_length=1, max_length=12)
    resources: Resources
    algorithm: Literal["astar", "ucs", "bfs"] = "astar"
    @model_validator(mode="after")
    def unique_ids(self):
        if len({p.id for p in self.patients}) != len(self.patients): raise ValueError("patient ids must be unique")
        return self

NAMES = {"astar": "A*", "ucs": "Uniform Cost Search", "bfs": "BFS"}

def run(req: PlanRequest, algo: str, sync_hospital: bool = True) -> dict:
    patient_dicts = [p.model_dump() for p in req.patients]
    pb = Problem(patient_dicts, req.resources.model_dump())
    r = search(pb, algo)
    out = {
        "success": r["found"],
        "algorithm": NAMES[algo],
        "total_cost": r["cost"],
        "statistics": {**r["stats"], "solution_depth": len(r["path"])},
        "message": r["reason"]
    }
    if r["found"]:
        out.update(build_plan(pb, r))
    else:
        out.update(plan=[], peak_usage={})
    out["capacity"] = req.resources.model_dump()

    # Bind to real hospital Ward -> Room -> Bed
    if sync_hospital:
        # Reset hospital allocations to fresh simulation baseline before binding
        hospital_instance.initialize_default_state()
        allocations = hospital_instance.bind_allocations(patient_dicts, out)
    else:
        # Temporary manager so comparison runs don't overwrite primary state
        temp_mgr = HospitalManager()
        allocations = temp_mgr.bind_allocations(patient_dicts, out)

    out["patient_allocations"] = allocations
    out["hospital_summary"] = hospital_instance.get_summary()
    return out

@app.get("/api/health")
def health():
    return {"status": "ok"}

@app.get("/api/hospital")
def get_hospital():
    """Returns complete hospital capacity, wards, rooms, beds, inventory, and analytics."""
    return hospital_instance.to_dict()

@app.get("/api/resources")
def get_resources():
    """Returns current resource utilization and inventory for medical equipment and beds."""
    return {
        "inventory": hospital_instance.get_resource_inventory(),
        "summary": hospital_instance.get_summary()
    }

@app.get("/api/patients")
def get_patients():
    """Returns patient allocation status (allocated if planned, waiting if not)."""
    if hospital_instance.active_allocations:
        return hospital_instance.active_allocations

    # If no plan executed yet, provide default initial sample patients in waiting status
    default_sample = [
        {"id": "P1", "severity": "critical", "waiting_time": 10, "unit": "icu", "ventilator": True, "surgery": False},
        {"id": "P2", "severity": "critical", "waiting_time": 5, "unit": "icu", "ventilator": False, "surgery": True},
        {"id": "P3", "severity": "serious", "waiting_time": 20, "unit": "emergency", "ventilator": False, "surgery": False},
        {"id": "P4", "severity": "moderate", "waiting_time": 30, "unit": "general", "ventilator": False, "surgery": False},
    ]
    return [
        {
            "patient_id": p["id"],
            "name": f"Patient {p['id']}",
            "severity": p["severity"].capitalize(),
            "priority": {"critical": "High", "serious": "Medium", "moderate": "Low"}.get(p["severity"], "Standard"),
            "status": "Waiting",
            "ward": "Not Allocated",
            "room": "—",
            "bed": "—",
            "assigned_resources": [],
            "waiting_time": p["waiting_time"],
            "cost": 0,
            "treatment": "Awaiting Emergency Triage Allocation"
        }
        for p in default_sample
    ]

@app.post("/api/hospital/reset")
def reset_hospital():
    """Resets the simulated hospital to default baseline capacity."""
    hospital_instance.initialize_default_state()
    return {"status": "reset", "summary": hospital_instance.get_summary()}

@app.post("/api/plan")
def plan(req: PlanRequest):
    return run(req, req.algorithm, sync_hospital=True)

@app.post("/api/compare")
def compare(req: PlanRequest):
    return [run(req, a, sync_hospital=(a == req.algorithm)) for a in ("bfs", "ucs", "astar")]

app.mount("/", StaticFiles(directory=pathlib.Path(__file__).parent.parent / "frontend", html=True))
