"""Admission control for the homogeneous compute pool."""

from __future__ import annotations

from dataclasses import dataclass

from .types import ResourceCapacity, ResourceRequest


@dataclass(frozen=True)
class Reservation:
    job_id: str
    request: ResourceRequest
    compute_slot: int


class ResourceLedger:
    """Mirrors CHIA resource tokens before a remote task is submitted.

    CHIA remains the enforcement layer on the cluster.  This ledger prevents
    the controller from filling CHIA's pending queue with work that cannot yet
    acquire a license and records which conceptual compute slot was occupied.
    """

    def __init__(self, capacity: ResourceCapacity) -> None:
        self.capacity = capacity.resolve()
        self._reservations: dict[str, Reservation] = {}
        self._free_slots = set(range(1, self.capacity.compute + 1))

    def used(self) -> ResourceRequest:
        return ResourceRequest(
            compute=len(self._reservations),
            sim_license=sum(
                reservation.request.sim_license
                for reservation in self._reservations.values()
            ),
            formal_license=sum(
                reservation.request.formal_license
                for reservation in self._reservations.values()
            ),
        )

    def available(self) -> ResourceRequest:
        used = self.used()
        return ResourceRequest(
            compute=self.capacity.compute - used.compute,
            sim_license=self.capacity.sim_license - used.sim_license,
            formal_license=self.capacity.formal_license - used.formal_license,
        )

    def can_fit(self, request: ResourceRequest) -> bool:
        available = self.available()
        return (
            request.compute <= available.compute
            and request.sim_license <= available.sim_license
            and request.formal_license <= available.formal_license
        )

    def reserve(self, job_id: str, request: ResourceRequest) -> Reservation:
        if job_id in self._reservations:
            raise ValueError(f"job already holds resources: {job_id}")
        if request.compute != 1:
            raise ValueError("core engine jobs must request exactly one compute slot")
        if not self.can_fit(request):
            raise RuntimeError(f"resources unavailable for {job_id}: {request.to_dict()}")
        slot = min(self._free_slots)
        self._free_slots.remove(slot)
        reservation = Reservation(job_id, request, slot)
        self._reservations[job_id] = reservation
        return reservation

    def release(self, job_id: str) -> Reservation:
        try:
            reservation = self._reservations.pop(job_id)
        except KeyError as exc:
            raise KeyError(f"job has no resource reservation: {job_id}") from exc
        self._free_slots.add(reservation.compute_slot)
        return reservation

    def reservation(self, job_id: str) -> Reservation:
        return self._reservations[job_id]

    def snapshot(self) -> dict:
        return {
            "capacity": self.capacity.to_dict(),
            "used": self.used().to_dict(),
            "available": self.available().to_dict(),
            "running": {
                job_id: {
                    "compute_slot": reservation.compute_slot,
                    "request": reservation.request.to_dict(),
                }
                for job_id, reservation in sorted(self._reservations.items())
            },
        }
