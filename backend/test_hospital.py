import pytest
from fastapi.testclient import TestClient
from hospital import HospitalManager, BedModel, RoomModel, WardModel
from main import app, PlanRequest

client = TestClient(app)

def test_hospital_initial_state():
    mgr = HospitalManager()
    summary = mgr.get_summary()
    assert summary["total_wards"] == 6
    assert summary["total_rooms"] == 42
    assert summary["total_beds"] == 120
    assert summary["available_beds"] == 28
    assert summary["occupied_beds"] == 82
    assert summary["reserved_beds"] == 6
    assert summary["beds_under_maintenance"] == 4

def test_ward_and_room_models():
    mgr = HospitalManager()
    assert "WARD-ICU" in mgr.wards
    icu = mgr.wards["WARD-ICU"]
    assert icu.name == "ICU"
    assert icu.total_rooms == 8
    assert icu.total_beds == 16
    summary = icu.summary()
    assert summary["total_beds"] == 16
    assert summary["utilization_pct"] > 0
    assert summary["occupied_beds"] + summary["available_beds"] + summary["reserved_beds"] + summary["maintenance_beds"] == 16

def test_available_bed_allocation_and_occupied_rejection():
    mgr = HospitalManager()
    avail_beds = [b for b in mgr.get_all_beds() if b.status == "AVAILABLE"]
    assert len(avail_beds) > 0
    test_bed = avail_beds[0]

    # Successfully allocate available bed
    allocated = mgr.allocate_bed(test_bed.bed_id, "TEST-P01")
    assert allocated.status == "OCCUPIED"
    assert allocated.patient_id == "TEST-P01"

    # Rejection: trying to allocate already occupied bed must raise ValueError
    with pytest.raises(ValueError, match="already OCCUPIED"):
        mgr.allocate_bed(test_bed.bed_id, "TEST-P02")

def test_maintenance_and_reserved_bed_rejection():
    mgr = HospitalManager()
    mnt_beds = [b for b in mgr.get_all_beds() if b.status == "MAINTENANCE"]
    assert len(mnt_beds) > 0
    with pytest.raises(ValueError, match="under MAINTENANCE"):
        mgr.allocate_bed(mnt_beds[0].bed_id, "TEST-P03")

    res_beds = [b for b in mgr.get_all_beds() if b.status == "RESERVED"]
    assert len(res_beds) > 0
    with pytest.raises(ValueError, match="is RESERVED"):
        mgr.allocate_bed(res_beds[0].bed_id, "TEST-P04")

def test_discharge_patient_updates_availability():
    mgr = HospitalManager()
    # Find an occupied bed
    occ_beds = [b for b in mgr.get_all_beds() if b.status == "OCCUPIED"]
    assert len(occ_beds) > 0
    initial_avail = mgr.get_summary()["available_beds"]
    patient_id = occ_beds[0].patient_id

    # Discharge the patient
    freed_bed = mgr.discharge_patient(patient_id)
    assert freed_bed is not None
    assert freed_bed.status == "AVAILABLE"
    assert freed_bed.patient_id is None
    new_avail = mgr.get_summary()["available_beds"]
    assert new_avail == initial_avail + 1

def test_api_hospital_endpoint():
    res = client.get("/api/hospital")
    assert res.status_code == 200
    data = res.json()
    assert "summary" in data
    assert data["summary"]["total_wards"] == 6
    assert data["summary"]["total_rooms"] == 42
    assert data["summary"]["total_beds"] == 120
    assert len(data["wards"]) == 6
    assert len(data["rooms"]) == 42
    assert len(data["beds"]) == 120
    assert "resource_inventory" in data
    assert "discharges" in data
    assert "analytics" in data

def test_api_resources_endpoint():
    res = client.get("/api/resources")
    assert res.status_code == 200
    data = res.json()
    assert "inventory" in data
    assert "ventilators" in data["inventory"]
    assert "operating_rooms" in data["inventory"]
    assert "doctors" in data["inventory"]
    assert "nurses" in data["inventory"]

def test_api_patients_before_and_after_planning():
    # Before plan
    client.post("/api/hospital/reset")
    res_before = client.get("/api/patients")
    assert res_before.status_code == 200
    patients_before = res_before.json()
    assert len(patients_before) > 0
    for p in patients_before:
        assert p["status"] == "Waiting"
        assert p["ward"] == "Not Allocated"

    # Run plan
    payload = {
        "patients": [
            {"id": "P1", "severity": "critical", "waiting_time": 10, "unit": "icu", "ventilator": True, "surgery": False},
            {"id": "P2", "severity": "critical", "waiting_time": 5, "unit": "icu", "ventilator": False, "surgery": True},
            {"id": "P3", "severity": "serious", "waiting_time": 20, "unit": "emergency", "ventilator": False, "surgery": False},
            {"id": "P4", "severity": "moderate", "waiting_time": 30, "unit": "general", "ventilator": False, "surgery": False}
        ],
        "resources": {
            "icu_beds": 2, "emergency_beds": 1, "general_beds": 1,
            "ventilators": 1, "operating_rooms": 1, "doctors": 2, "nurses": 3
        },
        "algorithm": "astar"
    }
    plan_res = client.post("/api/plan", json=payload)
    assert plan_res.status_code == 200
    plan_data = plan_res.json()
    assert plan_data["success"] is True
    assert "patient_allocations" in plan_data
    allocations = plan_data["patient_allocations"]
    assert len(allocations) == 4

    # Verify Ward -> Room -> Bed output
    for alloc in allocations:
        assert alloc["status"] == "allocated"
        assert alloc["ward"] in ["ICU", "Emergency Ward", "General Ward"]
        assert alloc["room"] is not None and len(alloc["room"]) > 0
        assert alloc["bed"] is not None and len(alloc["bed"]) > 0
        assert len(alloc["assigned_resources"]) > 0

    # Verify GET /api/patients now reflects allocated state
    res_after = client.get("/api/patients")
    assert res_after.status_code == 200
    patients_after = res_after.json()
    assert len(patients_after) == 4
    for p in patients_after:
        assert p["status"] == "allocated"
        assert p["ward"] in ["ICU", "Emergency Ward", "General Ward"]

def test_infeasible_allocation():
    # If resource capacity is 0 for a needed resource (e.g. ventilators=0)
    payload = {
        "patients": [
            {"id": "P1", "severity": "critical", "waiting_time": 10, "unit": "icu", "ventilator": True, "surgery": False}
        ],
        "resources": {
            "icu_beds": 2, "emergency_beds": 1, "general_beds": 1,
            "ventilators": 0, "operating_rooms": 1, "doctors": 2, "nurses": 3
        },
        "algorithm": "astar"
    }
    plan_res = client.post("/api/plan", json=payload)
    assert plan_res.status_code == 200
    plan_data = plan_res.json()
    assert plan_data["success"] is False
    assert "patient_allocations" in plan_data
    alloc = plan_data["patient_allocations"][0]
    assert alloc["status"] == "unallocated"
    assert alloc["ward"] is None
    assert alloc["room"] is None
    assert alloc["bed"] is None
    assert "ventilators" in alloc["reason"]
