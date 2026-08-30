import Lean

open Lean

private def parseProduct (path : String) : IO UInt32 := do
  let input <- IO.FS.readFile path
  Lean.initSearchPath (← Lean.findSysroot)
  unsafe Lean.enableInitializersExecution
  let env <- Lean.importModules #[{ module := `Mathlib }] {} (loadExts := true)
  match Lean.Parser.runParserCategory env `command input path with
  | .ok _ =>
      return 0
  | .error diagnostic =>
      IO.eprintln diagnostic
      return 10

def main (args : List String) : IO UInt32 := do
  match args with
  | [path] => parseProduct path
  | _ =>
      IO.eprintln "usage: parse_product SOURCE.lean"
      return 20
