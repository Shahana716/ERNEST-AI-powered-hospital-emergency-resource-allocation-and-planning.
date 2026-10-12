"""State-space model + BFS / UCS / A* for hospital resource planning.
Academic simulation only: severities and needs are given inputs, not clinical decisions.

STATE   : tuple of per-patient status (0 waiting, 1 admitted/treating, 2 released). Resource use is DERIVED
          from the tuple, so states are tiny, immutable, hashable.
ACTIONS : admit(i)   - only if every resource patient i needs is free (bed, ventilator, OR, doctor, nurse)
          release(i) - finish treatment of an admitted patient, freeing its resources
COST    : an action lasting d time units costs  d * sum(w_j for all patients still waiting)
          w_j = severity_weight + waiting_time // 10      (admit d = 1 + ventilator + 2*surgery; release d = 1)
          => total cost = weighted waiting time (critical patients waiting longer is penalised more)
H(n)    : sum over waiting j of w_j * d_j. Every waiting patient must at least wait for its own admission
          duration, so h never overestimates -> admissible (and consistent) -> A* is optimal.
GOAL    : no patient is still waiting.
"""
import heapq, itertools, time
from collections import deque

SEV_W = {"critical": 10, "serious": 5, "moderate": 2}
UNIT_KEY = {"icu": "icu_beds", "emergency": "emergency_beds", "general": "general_beds"}
RES_KEYS = ["icu_beds", "emergency_beds", "general_beds", "ventilators", "operating_rooms", "doctors", "nurses"]


class Problem:
    def __init__(self, patients: list[dict], resources: dict):
        self.p, self.cap = patients, resources
        self.w = [SEV_W[x["severity"]] + x["waiting_time"] // 10 for x in patients]
        self.need = []
        for x in patients:
            n = {k: 0 for k in RES_KEYS}
            n[UNIT_KEY[x["unit"]]] = 1
            n["ventilators"] = int(x["ventilator"])
            n["operating_rooms"] = int(x["surgery"])
            n["doctors"], n["nurses"] = 1, 1
            self.need.append(n)
        self.dur = [1 + int(x["ventilator"]) + 2 * int(x["surgery"]) for x in patients]
        self.start = tuple(0 for _ in patients)

    def infeasible_reason(self):
        for x, n in zip(self.p, self.need):
            for k, v in n.items():
                if v > self.cap[k]:
                    return f"{x['id']} needs {k} but total capacity is {self.cap[k]}"
        return None

    def usage(self, s):
        u = {k: 0 for k in RES_KEYS}
        for i, st in enumerate(s):
            if st == 1:
                for k, v in self.need[i].items():
                    u[k] += v
        return u

    def actions(self, s):
        u, out = self.usage(s), []
        for i, st in enumerate(s):
            if st == 0 and all(u[k] + v <= self.cap[k] for k, v in self.need[i].items()):
                out.append(("admit", i))
            elif st == 1:
                out.append(("release", i))
        return out

    def step_cost(self, s, a):
        d = self.dur[a[1]] if a[0] == "admit" else 1
        return d * sum(self.w[j] for j, st in enumerate(s) if st == 0)

    def apply(self, s, a):  # returns a NEW state, never mutates
        t = list(s); t[a[1]] = 1 if a[0] == "admit" else 2
        return tuple(t)

    def is_goal(self, s):
        return 0 not in s

    def h(self, s):
        return sum(self.w[j] * self.dur[j] for j, st in enumerate(s) if st == 0)


def search(pb: Problem, algo: str, max_states: int = 300_000) -> dict:
    t0 = time.perf_counter()
    stats = {"states_explored": 0, "states_generated": 1, "max_frontier": 1}
    def done(ok, goal=None, g=0, parent=None, reason=None):
        path = []
        while ok and parent[goal][0] is not None:
            prev, a, c = parent[goal]; path.append((prev, a, c)); goal = prev
        stats["execution_time_ms"] = round((time.perf_counter() - t0) * 1000, 3)
        return {"found": ok, "cost": g, "path": path[::-1], "stats": stats, "reason": reason}

    reason = pb.infeasible_reason()
    if reason:
        return done(False, reason=reason)
    parent, best, tie = {pb.start: (None, None, 0)}, {pb.start: 0}, itertools.count()
    use_heap = algo in ("astar", "ucs")
    hf = pb.h if algo == "astar" else (lambda s: 0)
    frontier = [(hf(pb.start), next(tie), 0, pb.start)] if use_heap else deque([(0, pb.start)])
    while frontier:
        if use_heap:
            _, _, g, s = heapq.heappop(frontier)
            if g > best[s]: continue                      # stale duplicate
            if pb.is_goal(s): return done(True, s, g, parent)
        else:
            g, s = frontier.popleft()
        stats["states_explored"] += 1
        if stats["states_explored"] > max_states:
            return done(False, reason=f"search limit of {max_states} states reached")
        for a in pb.actions(s):
            ns, g2 = pb.apply(s, a), g + pb.step_cost(s, a)
            if ns in best and best[ns] <= g2: continue
            best[ns], parent[ns] = g2, (s, a, pb.step_cost(s, a))
            stats["states_generated"] += 1
            if use_heap: heapq.heappush(frontier, (g2 + hf(ns), next(tie), g2, ns))
            else:
                if pb.is_goal(ns): return done(True, ns, g2, parent)   # BFS: fewest actions, ignores cost
                frontier.append((g2, ns))
        stats["max_frontier"] = max(stats["max_frontier"], len(frontier))
    return done(False, reason="no goal reachable")


def build_plan(pb: Problem, result: dict) -> dict:
    """Replay the path to name concrete beds and explain each step."""
    slots = {k: set() for k in RES_KEYS}; held, plan, peak = {}, [], {k: 0 for k in RES_KEYS}
    for n, (s, (kind, i), c) in enumerate(result["path"], 1):
        p = pb.p[i]
        if kind == "admit":
            held[i] = {}
            for k, v in pb.need[i].items():
                if v:
                    idx = min(x for x in range(pb.cap[k]) if x not in slots[k]); slots[k].add(idx); held[i][k] = idx
            res = ", ".join(f"{k.replace('_',' ')} #{x+1}" for k, x in held[i].items() if k not in ("doctors", "nurses"))
            why = f"{p['severity'].title()} (weight {pb.w[i]}): admitted while {sum(1 for t in s if t==0)-1} others wait"
        else:
            for k, x in held.pop(i).items(): slots[k].discard(x)
            res, why = "frees resources", "Released to unblock waiting patients"
        for k, v in pb.usage(pb.apply(s, (kind, i))).items(): peak[k] = max(peak[k], v)
        plan.append({"step": n, "patient": p["id"], "action": kind, "resource": res, "reason": why, "cost": c})
    return {"plan": plan, "peak_usage": peak}
