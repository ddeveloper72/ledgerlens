# Phase 3C implementation notes

## Audit summary

The Phase 3B baseline passes 115 tests. The repository already has household-only
income allocation, selected-date health, balance snapshots, deterministic guidance,
and read-only match proposals. Phase 3C will extend those foundations rather than
introduce parallel forecast calculations.

Highest-risk findings:

- partial contribution matches are currently represented by one transaction and
  their expected amount can be counted as received;
- aggregate fixed and percentage allocations are not validated across overlapping
  effective periods;
- the payment-failure threshold currently includes the safety buffer;
- selected-date balance provenance is not labelled precisely;
- household summaries, calibration reporting/history, and weighted confidence are
  not yet modelled.

## Implementation plan

1. Add auditable contribution match details and canonical received/outstanding
   calculations, preserving existing reconciliation records.
2. Validate mixed active allocations at every effective-date boundary.
3. expose distinct payment-failure, overdraft-avoidance, and safety-buffer amounts,
   plus selected-date balance provenance.
4. Add privacy-preserving household spending summaries and budget assumption
   history with explicit POST-only changes.
5. Add forecast-versus-actual calibration, period reporting, weighted confidence,
   and explainable guidance.
6. Update the Daily Health and Forecast Accuracy interfaces, migration coverage,
   README, and focused regression tests.

All fixtures and examples remain generic. Period end dates are inclusive unless a
service explicitly documents otherwise.
