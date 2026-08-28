import Mathlib

open BigOperators

theorem supernova_goal1_runtime_smoke :
    (∑ i in Finset.range 5, i) = 10 := by
  norm_num
