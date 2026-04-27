Dear Arian,

May god help you.

work flow:
login(select role) -> SetOperatingMode = T1 -> spoc_select_run (request spoc and select program) -> SetOperatingMode = AUT -> spoc_select_run(to run program) 
-> logoutKUKA

proper workflow:
login -> SetOperatingMode = T1 -> request SPOC -> Select program to run -> SetOperatingMode = AUT -> start program -> logout

switch role is there cause it works differently then loggingout and logging in again
Added full SPOC function in spoc_control3 to request and release so you can see how it works 
Added select program so you can see the logic behind it in kuka_program_selection2.py
Spoc_select_run.py is the full logic of requesting spoc selecting and running a program

I will try to figure out the 403 error when starting a program.

Best of luck, Ali.
