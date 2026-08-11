# Five-Node Integration Test

Do not begin until one complete node passes S15.

Matrix:
- CTRL-01 owns global drawers 01-10
- CTRL-02 owns 11-20
- CTRL-03 owns 21-30
- CTRL-04 owns 31-40
- CTRL-05 owns 41-50

Required final routing test:
trigger all 50 global drawer IDs and verify exactly one physical LED activates each time, on the expected controller and local index.
