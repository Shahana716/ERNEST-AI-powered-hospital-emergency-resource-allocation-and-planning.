"""Hospital capacity and resource management model.
Academic simulation data - not for clinical decisions.
"""
from __future__ import annotations
from typing import Literal, Optional, Dict, Any, List
from pydantic import BaseModel, Field

BedStatusType = Literal["AVAILABLE", "OCCUPIED", "RESERVED", "MAINTENANCE"]


class BedModel(BaseModel):
    bed_id: str
    room_id: str
    ward_id: str
    status: BedStatusType = "AVAILABLE"
    patient_id: Optional[str] = None


class RoomModel(BaseModel):
    room_id: str
    ward_id: str
    name: str
    total_beds: int
    status: str = "Available"
    beds: List[BedModel] = Field(default_factory=list)

    @property
    def occupied_beds(self) -> int:
        return sum(1 for b in self.beds if b.status == "OCCUPIED")

    @property
    def available_beds(self) -> int:
        return sum(1 for b in self.beds if b.status == "AVAILABLE")

    def update_status(self):
        occ = self.occupied_beds
        tot = len(self.beds) or self.total_beds
        if occ == 0:
            self.status = "Available"
        elif occ >= tot:
            self.status = "Full"
        else:
            self.status = "Partially Occupied"


class WardModel(BaseModel):
    ward_id: str
    name: str
    type: str  # icu, emergency, general, surgical, pediatric, observation
    total_rooms: int
    total_beds: int
    rooms: List[RoomModel] = Field(default_factory=list)

    def summary(self) -> Dict[str, Any]:
        all_beds = [b for r in self.rooms for b in r.beds]
        tot = len(all_beds) or self.total_beds
        occ = sum(1 for b in all_beds if b.status == "OCCUPIED")
        avail = sum(1 for b in all_beds if b.status == "AVAILABLE")
        res = sum(1 for b in all_beds if b.status == "RESERVED")
        maint = sum(1 for b in all_beds if b.status == "MAINTENANCE")
        util = round((occ / tot * 100), 1) if tot > 0 else 0.0

        if util >= 85:
            st = "Critical Shortage" if avail == 0 else "High Load"
        elif util >= 65:
            st = "Normal Load"
        else:
            st = "Healthy Availability"

        return {
            "ward_id": self.ward_id,
            "name": self.name,
            "type": self.type,
            "total_rooms": len(self.rooms),
            "total_beds": tot,
            "occupied_beds": occ,
            "available_beds": avail,
            "reserved_beds": res,
            "maintenance_beds": maint,
            "utilization_pct": util,
            "current_patients": occ,
            "status": st,
        }


class DischargeRecord(BaseModel):
    patient_id: str
    patient_name: str
    ward: str
    room: str
    bed: str
    admission_date: str
    expected_discharge: str
    status: Literal["Admitted", "Discharged", "Pending"]


class HospitalManager:
    """Manages the full simulated hospital capacity and resource state."""

    def __init__(self):
        self.wards: Dict[str, WardModel] = {}
        self.discharges: List[DischargeRecord] = []
        self.active_allocations: List[Dict[str, Any]] = []
        self.last_planned_patients: List[Dict[str, Any]] = []
        self.initialize_default_state()

    def initialize_default_state(self):
        """Builds default simulated hospital matching:
        6 Wards, 42 Rooms, 120 Beds.
        Initial counts: Occupied 82, Reserved 6, Maintenance 4, Available 28.
        """
        self.wards.clear()
        self.discharges.clear()
        self.active_allocations.clear()

        # Ward specifications: (ward_id, name, type, room_prefix, num_rooms, beds_per_room)
        specs = [
            ("WARD-ICU", "ICU", "icu", "ICU", 8, 2),                  # 8 rooms, 16 beds
            ("WARD-ER", "Emergency Ward", "emergency", "ER", 10, 3),   # 10 rooms, 30 beds
            ("WARD-GW", "General Ward", "general", "GW", 12, 4),      # 12 rooms, 48 beds
            ("WARD-SW", "Surgical Ward", "surgical", "SW", 4, 2),     # 4 rooms, 8 beds
            ("WARD-PED", "Pediatric Ward", "pediatric", "PED", 4, 2), # 4 rooms, 8 beds
            ("WARD-OBS", "Observation Ward", "observation", "OBS", 4, 2.5), # 4 rooms: 2x3 + 2x2 = 10 beds
        ]

        # Target distribution of 120 beds:
        # Occupied: 82, Reserved: 6, Maintenance: 4, Available: 28.
        # Ward distribution:
        # ICU (16): Occ 13, Res 1, Mnt 0, Avail 2 (utilization 81%)
        # ER (30): Occ 21, Res 2, Mnt 1, Avail 6 (utilization 70%)
        # GW (48): Occ 33, Res 2, Mnt 2, Avail 11 (utilization 69%)
        # SW (8): Occ 5, Res 1, Mnt 0, Avail 2 (utilization 62.5%)
        # PED (8): Occ 5, Res 0, Mnt 1, Avail 2 (utilization 62.5%)
        # OBS (10): Occ 5, Res 0, Mnt 0, Avail 5 (utilization 50%)
        # Sums: Occ = 13+21+33+5+5+5 = 82
        #       Res = 1+2+2+1+0+0 = 6
        #       Mnt = 0+1+2+0+1+0 = 4
        #       Avail = 2+6+11+2+2+5 = 28
        #       Total = 82 + 6 + 4 + 28 = 120 beds!

        ward_targets = {
            "WARD-ICU": {"occ": 13, "res": 1, "mnt": 0, "avail": 2},
            "WARD-ER":  {"occ": 21, "res": 2, "mnt": 1, "avail": 6},
            "WARD-GW":  {"occ": 33, "res": 2, "mnt": 2, "avail": 11},
            "WARD-SW":  {"occ": 5,  "res": 1, "mnt": 0, "avail": 2},
            "WARD-PED": {"occ": 5,  "res": 0, "mnt": 1, "avail": 2},
            "WARD-OBS": {"occ": 5,  "res": 0, "mnt": 0, "avail": 5},
        }

        patient_seq = 100

        for ward_id, name, w_type, r_prefix, num_rooms, beds_per_room in specs:
            rooms_list = []
            targets = ward_targets[ward_id]
            needed_occ = targets["occ"]
            needed_res = targets["res"]
            needed_mnt = targets["mnt"]

            total_beds_ward = 0
            for r_idx in range(1, num_rooms + 1):
                room_id = f"{r_prefix}-{r_idx:02d}"
                room_name = f"Room {room_id}"

                if w_type == "observation":
                    num_beds = 3 if r_idx <= 2 else 2
                else:
                    num_beds = int(beds_per_room)

                total_beds_ward += num_beds
                beds_in_room = []
                letters = ["A", "B", "C", "D", "E"]

                for b_idx in range(num_beds):
                    bed_id = f"{room_id}-{letters[b_idx]}"

                    if needed_occ > 0:
                        status: BedStatusType = "OCCUPIED"
                        patient_id = f"SIM-P{patient_seq:03d}"
                        patient_seq += 1
                        needed_occ -= 1
                    elif needed_res > 0:
                        status = "RESERVED"
                        patient_id = None
                        needed_res -= 1
                    elif needed_mnt > 0:
                        status = "MAINTENANCE"
                        patient_id = None
                        needed_mnt -= 1
                    else:
                        status = "AVAILABLE"
                        patient_id = None

                    beds_in_room.append(BedModel(
                        bed_id=bed_id,
                        room_id=room_id,
                        ward_id=ward_id,
                        status=status,
                        patient_id=patient_id,
                    ))

                r_model = RoomModel(
                    room_id=room_id,
                    ward_id=ward_id,
                    name=room_name,
                    total_beds=num_beds,
                    beds=beds_in_room,
                )
                r_model.update_status()
                rooms_list.append(r_model)

            ward = WardModel(
                ward_id=ward_id,
                name=name,
                type=w_type,
                total_rooms=len(rooms_list),
                total_beds=total_beds_ward,
                rooms=rooms_list,
            )
            self.wards[ward_id] = ward

        # Initialize realistic simulated discharge records
        self.discharges = [
            DischargeRecord(patient_id="SIM-P101", patient_name="Robert Chen", ward="ICU", room="ICU-01", bed="ICU-01-A", admission_date="01 Oct 2026", expected_discharge="06 Oct 2026", status="Discharged"),
            DischargeRecord(patient_id="SIM-P102", patient_name="Eleanor Vance", ward="ICU", room="ICU-01", bed="ICU-01-B", admission_date="03 Oct 2026", expected_discharge="07 Oct 2026", status="Pending"),
            DischargeRecord(patient_id="SIM-P105", patient_name="Marcus Brody", ward="Emergency Ward", room="ER-02", bed="ER-02-B", admission_date="05 Oct 2026", expected_discharge="06 Oct 2026", status="Discharged"),
            DischargeRecord(patient_id="SIM-P108", patient_name="Amina Al-Mansoor", ward="Emergency Ward", room="ER-03", bed="ER-03-A", admission_date="04 Oct 2026", expected_discharge="08 Oct 2026", status="Admitted"),
            DischargeRecord(patient_id="SIM-P115", patient_name="David Miller", ward="General Ward", room="GW-01", bed="GW-01-C", admission_date="29 Sep 2026", expected_discharge="06 Oct 2026", status="Discharged"),
            DischargeRecord(patient_id="SIM-P120", patient_name="Sofia Rodriguez", ward="General Ward", room="GW-04", bed="GW-04-A", admission_date="02 Oct 2026", expected_discharge="06 Oct 2026", status="Pending"),
            DischargeRecord(patient_id="SIM-P125", patient_name="Liam O'Connor", ward="Surgical Ward", room="SW-01", bed="SW-01-A", admission_date="03 Oct 2026", expected_discharge="09 Oct 2026", status="Admitted"),
            DischargeRecord(patient_id="SIM-P130", patient_name="Grace Kim", ward="Pediatric Ward", room="PED-01", bed="PED-01-A", admission_date="04 Oct 2026", expected_discharge="07 Oct 2026", status="Admitted"),
            DischargeRecord(patient_id="SIM-P135", patient_name="James Wilson", ward="Observation Ward", room="OBS-01", bed="OBS-01-B", admission_date="05 Oct 2026", expected_discharge="06 Oct 2026", status="Discharged"),
            DischargeRecord(patient_id="SIM-P140", patient_name="Clara Oswald", ward="General Ward", room="GW-06", bed="GW-06-B", admission_date="01 Oct 2026", expected_discharge="06 Oct 2026", status="Pending"),
        ]

    def get_summary(self) -> Dict[str, Any]:
        all_beds = [b for w in self.wards.values() for r in w.rooms for b in r.beds]
        tot_beds = len(all_beds)
        occ = sum(1 for b in all_beds if b.status == "OCCUPIED")
        avail = sum(1 for b in all_beds if b.status == "AVAILABLE")
        res = sum(1 for b in all_beds if b.status == "RESERVED")
        mnt = sum(1 for b in all_beds if b.status == "MAINTENANCE")
        tot_rooms = sum(len(w.rooms) for w in self.wards.values())

        return {
            "total_wards": len(self.wards),
            "total_rooms": tot_rooms,
            "total_beds": tot_beds,
            "available_beds": avail,
            "occupied_beds": occ,
            "reserved_beds": res,
            "beds_under_maintenance": mnt,
            "total_patients": occ,
            "current_admissions": 14,
            "today_discharges": 8,
            "expected_discharges": 12,
            "pending_discharges": 6,
            "beds_expected_to_become_available": 18,
        }

    def get_resource_inventory(self) -> Dict[str, Dict[str, Any]]:
        """Returns inventory for all 7 medical resources."""
        all_beds = [b for w in self.wards.values() for r in w.rooms for b in r.beds]

        def ward_counts(w_type: str):
            beds = [b for w in self.wards.values() if w.type == w_type for r in w.rooms for b in r.beds]
            tot = len(beds)
            occ = sum(1 for b in beds if b.status == "OCCUPIED")
            avail = sum(1 for b in beds if b.status == "AVAILABLE")
            res = sum(1 for b in beds if b.status == "RESERVED")
            util = round(occ / tot * 100, 1) if tot > 0 else 0.0
            return {"total": tot, "available": avail, "occupied": occ, "reserved": res, "utilization_pct": util}

        # Ventilators: simulated equipment capacity
        # 10 total: 6 in use, 1 reserved, 3 available -> 70%
        # Operating rooms: 6 total: 4 in use, 1 reserved, 1 available -> 83%
        # Doctors: 18 total: 12 on duty/occupied, 2 reserved, 4 available -> 78%
        # Nurses: 32 total: 22 on duty/occupied, 4 reserved, 6 available -> 81%
        return {
            "icu_beds": ward_counts("icu"),
            "emergency_beds": ward_counts("emergency"),
            "general_beds": ward_counts("general"),
            "ventilators": {"total": 10, "available": 3, "occupied": 6, "reserved": 1, "utilization_pct": 70.0},
            "operating_rooms": {"total": 6, "available": 1, "occupied": 4, "reserved": 1, "utilization_pct": 83.3},
            "doctors": {"total": 18, "available": 4, "occupied": 12, "reserved": 2, "utilization_pct": 77.8},
            "nurses": {"total": 32, "available": 6, "occupied": 22, "reserved": 4, "utilization_pct": 81.3},
        }

    def get_all_beds(self) -> List[BedModel]:
        return [b for w in self.wards.values() for r in w.rooms for b in r.beds]

    def find_bed(self, bed_id: str) -> Optional[BedModel]:
        for w in self.wards.values():
            for r in w.rooms:
                for b in r.beds:
                    if b.bed_id == bed_id:
                        return b
        return None

    def allocate_bed(self, bed_id: str, patient_id: str) -> BedModel:
        bed = self.find_bed(bed_id)
        if not bed:
            raise ValueError(f"Bed {bed_id} not found in hospital.")
        if bed.status == "OCCUPIED":
            raise ValueError(f"Bed {bed_id} is already OCCUPIED by patient {bed.patient_id}.")
        if bed.status == "MAINTENANCE":
            raise ValueError(f"Bed {bed_id} is under MAINTENANCE and cannot be allocated.")
        if bed.status == "RESERVED":
            raise ValueError(f"Bed {bed_id} is RESERVED and cannot be allocated directly.")

        bed.status = "OCCUPIED"
        bed.patient_id = patient_id

        # Update parent room status
        for w in self.wards.values():
            for r in w.rooms:
                if r.room_id == bed.room_id:
                    r.update_status()
                    break

        return bed

    def release_bed(self, bed_id: str) -> BedModel:
        bed = self.find_bed(bed_id)
        if not bed:
            raise ValueError(f"Bed {bed_id} not found in hospital.")
        bed.status = "AVAILABLE"
        bed.patient_id = None

        for w in self.wards.values():
            for r in w.rooms:
                if r.room_id == bed.room_id:
                    r.update_status()
                    break

        return bed

    def discharge_patient(self, patient_id: str) -> Optional[BedModel]:
        """Discharges a patient and frees their bed."""
        for b in self.get_all_beds():
            if b.patient_id == patient_id:
                self.release_bed(b.bed_id)
                # update discharge record if found
                for rec in self.discharges:
                    if rec.patient_id == patient_id:
                        rec.status = "Discharged"
                return b
        return None

    def get_available_beds_for_ward_type(self, ward_type: str) -> List[BedModel]:
        """Returns list of available beds in wards matching unit type."""
        res = []
        for w in self.wards.values():
            if w.type == ward_type:
                for r in w.rooms:
                    for b in r.beds:
                        if b.status == "AVAILABLE":
                            res.append(b)
        return res

    def bind_allocations(self, patients: List[Dict[str, Any]], plan_result: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Takes scenario patients and the AI search plan result, and maps each
        patient to a real Ward -> Room -> Bed based on the AI plan.
        """
        self.last_planned_patients = patients
        allocations: List[Dict[str, Any]] = []

        if not plan_result.get("success"):
            # Plan was infeasible or timed out
            reason = plan_result.get("message") or "AI planner could not find a feasible allocation."
            for p in patients:
                allocations.append({
                    "patient_id": p["id"],
                    "name": f"Patient {p['id']}",
                    "severity": p["severity"].capitalize(),
                    "priority": {"critical": "High", "serious": "Medium", "moderate": "Low"}.get(p["severity"], "Standard"),
                    "status": "unallocated",
                    "ward": None,
                    "room": None,
                    "bed": None,
                    "reason": reason,
                    "assigned_resources": [],
                    "waiting_time": p.get("waiting_time", 0),
                    "cost": 0,
                    "treatment": "Pending resource availability",
                })
            self.active_allocations = allocations
            return allocations

        # The plan succeeded. Examine the steps in plan_result["plan"]
        # In the AI plan, actions are: step, patient, action ("admit"/"release"), resource, reason, cost
        plan_steps = plan_result.get("plan", [])

        # Find which patients were admitted
        admitted_patient_steps = {}
        for step in plan_steps:
            if step["action"] == "admit":
                admitted_patient_steps[step["patient"]] = step

        # Get available physical beds by ward type
        available_beds_pool = {
            "icu": self.get_available_beds_for_ward_type("icu"),
            "emergency": self.get_available_beds_for_ward_type("emergency"),
            "general": self.get_available_beds_for_ward_type("general"),
        }

        # Track which beds are assigned in this simulation run
        assigned_bed_ids = set()

        # Equipment naming pools for realistic assignment
        vent_counter = 1
        doc_counter = 1
        nurse_counter = 1

        for p in patients:
            pid = p["id"]
            unit = p.get("unit", "general")
            sev = p.get("severity", "moderate")
            priority = {"critical": "High", "serious": "Medium", "moderate": "Low"}.get(sev, "Standard")

            if pid in admitted_patient_steps:
                step = admitted_patient_steps[pid]
                step_cost = step.get("cost", 0)

                # Pick an available bed from the designated ward type
                pool = available_beds_pool.get(unit, [])
                assigned_bed: Optional[BedModel] = None
                for candidate in pool:
                    if candidate.bed_id not in assigned_bed_ids:
                        assigned_bed = candidate
                        assigned_bed_ids.add(candidate.bed_id)
                        break

                ward_name = {"icu": "ICU", "emergency": "Emergency Ward", "general": "General Ward"}.get(unit, "General Ward")
                room_id = assigned_bed.room_id if assigned_bed else f"{unit.upper()}-01"
                bed_id = assigned_bed.bed_id if assigned_bed else f"{room_id}-A"

                # Mark bed as occupied by this scenario patient in hospital state
                if assigned_bed:
                    try:
                        self.allocate_bed(assigned_bed.bed_id, pid)
                    except ValueError:
                        pass

                # Build assigned resources list
                assigned_res = [f"{ward_name} Bed ({bed_id})"]
                treatment_desc = f"{sev.capitalize()} Care & Clinical Observation"

                if p.get("ventilator"):
                    vent_id = f"Ventilator V-{vent_counter:02d}"
                    vent_counter += 1
                    assigned_res.append(vent_id)
                    treatment_desc = f"Critical Respiratory Support & {vent_id}"

                if p.get("surgery"):
                    assigned_res.append("Operating Room OR-01")
                    treatment_desc += " with Surgical Intervention"

                doc_id = f"Doctor D-{doc_counter:02d}"
                nurse_id = f"Nurse N-{nurse_counter:02d}"
                doc_counter += 1
                nurse_counter += 1
                assigned_res.extend([doc_id, nurse_id])

                allocations.append({
                    "patient_id": pid,
                    "name": f"Patient {pid}",
                    "severity": sev.capitalize(),
                    "priority": priority,
                    "status": "allocated",
                    "ward": ward_name,
                    "room": room_id,
                    "bed": bed_id,
                    "assigned_resources": assigned_res,
                    "treatment": treatment_desc,
                    "waiting_time": p.get("waiting_time", 0),
                    "cost": step_cost,
                    "plan_step": step["step"],
                    "reason": step.get("reason", "Optimal allocation determined by AI Planner"),
                })
            else:
                # Patient was not admitted in this plan
                allocations.append({
                    "patient_id": pid,
                    "name": f"Patient {pid}",
                    "severity": sev.capitalize(),
                    "priority": priority,
                    "status": "unallocated",
                    "ward": None,
                    "room": None,
                    "bed": None,
                    "reason": "Search completed without scheduling admission",
                    "assigned_resources": [],
                    "waiting_time": p.get("waiting_time", 0),
                    "cost": 0,
                    "treatment": "Waiting in triage",
                })

        self.active_allocations = allocations
        return allocations

    def to_dict(self) -> Dict[str, Any]:
        """Full representation for GET /api/hospital."""
        summary = self.get_summary()
        ward_summaries = [w.summary() for w in self.wards.values()]

        # Flatten rooms and beds for easy consumption and filtering
        rooms_list = []
        beds_list = []
        for w in self.wards.values():
            for r in w.rooms:
                rooms_list.append({
                    "room_id": r.room_id,
                    "ward_id": w.ward_id,
                    "ward_name": w.name,
                    "ward_type": w.type,
                    "name": r.name,
                    "total_beds": len(r.beds),
                    "occupied_beds": r.occupied_beds,
                    "available_beds": r.available_beds,
                    "status": r.status,
                })
                for b in r.beds:
                    beds_list.append({
                        "bed_id": b.bed_id,
                        "room_id": r.room_id,
                        "ward_id": w.ward_id,
                        "ward_name": w.name,
                        "ward_type": w.type,
                        "status": b.status,
                        "patient_id": b.patient_id or "—",
                    })

        inventory = self.get_resource_inventory()

        # Capacity Analytics data
        analytics = {
            "bed_occupancy_rate": round(summary["occupied_beds"] / (summary["total_beds"] or 1) * 100, 1),
            "ward_utilization": [{"name": ws["name"], "utilization": ws["utilization_pct"], "status": ws["status"]} for ws in ward_summaries],
            "admissions_vs_discharges": {
                "today_admissions": summary["current_admissions"],
                "today_discharges": summary["today_discharges"],
                "expected_discharges": summary["expected_discharges"],
            },
            "available_capacity": {
                "available_beds": summary["available_beds"],
                "occupied_beds": summary["occupied_beds"],
                "reserved_beds": summary["reserved_beds"],
                "maintenance_beds": summary["beds_under_maintenance"],
            },
        }

        return {
            "hospital_name": "St. Jude Metropolitan Medical Center (Simulation Environment)",
            "environment": "Simulation Environment",
            "summary": summary,
            "wards": ward_summaries,
            "rooms": rooms_list,
            "beds": beds_list,
            "resource_inventory": inventory,
            "discharges": [d.model_dump() for d in self.discharges],
            "analytics": analytics,
            "patient_allocations": self.active_allocations,
        }


# Global singleton instance for the app session
hospital_instance = HospitalManager()
