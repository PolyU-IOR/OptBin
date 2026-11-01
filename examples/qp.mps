NAME qptest
OBJSENSE
 MIN
ROWS
 N obj
 E eq
 L qc
COLUMNS
 X obj 1 eq 1
 X qc -3
 Y obj -2 eq -1
 Y qc 4
RHS
 rhs obj -7 eq 2
 rhs qc 5
BOUNDS
 FR b X
 UP b Y 10
QUADOBJ
 X X 2
 X Y 0.5
 Y Y 4
ENDATA
