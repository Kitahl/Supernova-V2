/-
Copyright (c) 2026 Project Supernova contributors.
Released under Apache 2.0.

This process receives data exports only. It never elaborates candidate Lean source.
-/
import Comparator
import Export.Parse

namespace Supernova.CheckExports

structure Config where
  theorem_names : Array String
  permitted_axioms : Array String
  deriving Lean.FromJson

def stringStream (s : String) : BaseIO IO.FS.Stream := do
  let ref <- IO.mkRef { data := s.toByteArray }
  return IO.FS.Stream.ofBuffer ref

def primitiveTargets : Array Lean.Name := #[
  ``Nat.add,
  ``Nat.sub,
  ``Nat.mul,
  ``Nat.pow,
  ``Nat.gcd,
  ``Nat.div,
  ``Nat.mod,
  ``Nat.beq,
  ``Nat.ble,
  ``Nat.land,
  ``Nat.lor,
  ``Nat.xor,
  ``Nat.shiftLeft,
  ``Nat.shiftRight,
  ``String.ofList
]

def check (challengeText solutionText : String) (cfg : Config) : IO Unit := do
  let challenge <- Export.parseStream (← stringStream challengeText)
  let solution <- Export.parseStream (← stringStream solutionText)
  let theoremNames := cfg.theorem_names.map String.toName
  let legalAxioms := cfg.permitted_axioms.map String.toName
  let targets := theoremNames ++ legalAxioms
  IO.ofExcept <| Comparator.compareAt challenge solution targets #[] primitiveTargets
  IO.ofExcept <| Comparator.checkAxioms solution theoremNames #[] legalAxioms

end Supernova.CheckExports

def main (args : List String) : IO Unit := do
  let [challengePath, solutionPath, configPath] := args
    | throw <| IO.userError "expected exactly three arguments"
  if challengePath.isEmpty || solutionPath.isEmpty || configPath.isEmpty then
    throw <| IO.userError "expected exactly three arguments"
  let challenge <- IO.FS.readFile challengePath
  let solution <- IO.FS.readFile solutionPath
  let configText <- IO.FS.readFile configPath
  let configJson <- IO.ofExcept <| Lean.Json.parse configText
  let config <- IO.ofExcept <| Lean.FromJson.fromJson? configJson
  Supernova.CheckExports.check challenge solution config
