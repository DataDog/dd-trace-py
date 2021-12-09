seqdiag {
  // normal edge and doted edge
  A -> B [label = "normal edge"];
  B --> C [label = "dotted edge"];

  B <-- C [label = "return dotted edge"];
  A <- B [label = "return edge"];

  // asynchronus edge
  A ->> B [label = "asynchronus edge"];
  B -->> C [label = "asynchronus dotted edge"];

  B <<-- C [label = "return asynchronus doted edge"];
  A <<- B [label = "return asynchronus edge"];

  // self referenced edge
  A -> A [label = "self reference edge"];
}