# Hospital Emergency Resource Planning — State-Space Search & A*
Academic planning/simulation tool. **Not** a clinical decision system: severity and resource needs are given inputs.

## Run
    cd backend && pip install -r requirements.txt
    uvicorn main:app --reload      # open http://localhost:8000
    python -c "import test_engine"  # or: pytest

## AI formulation (see ai_engine/core.py docstring)
- **State**: tuple of patient statuses (0 waiting, 1 treating, 2 released); resource use is derived -> immutable, hashable, small (3^n).
- **Actions**: admit(i) if every needed bed/ventilator/OR/doctor/nurse is free; release(i) to free resources.
- **Goal**: no patient waiting.  **Infeasible** if any patient's need exceeds total capacity (clear message, no crash).
- **Cost**: action of duration d costs d * sum(weights of patients still waiting); weight = severity (10/5/2) + wait//10.
- **Heuristic**: h = sum over waiting j of weight_j * duration_j. Each waiting patient must wait at least its own admission time, so h <= true cost: admissible and consistent, hence A* is optimal.
- **Algorithms**: A* (f=g+h, heap), UCS (f=g), BFS (FIFO, fewest actions, ignores cost). Duplicate detection via best-g dict; deterministic tie-break counter.

## API
`GET /api/health` · `POST /api/plan` · `POST /api/compare` (runs BFS, UCS, A*; all numbers measured live).
Body: `{"patients":[{"id":"P1","severity":"critical","waiting_time":10,"unit":"icu","ventilator":true,"surgery":false}],"resources":{"icu_beds":2,...},"algorithm":"astar"}`

## Limitations
Max 12 patients (state space 3^n); unit-time model; no care-level transfers; one doctor + one nurse per patient.
