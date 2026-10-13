       IDENTIFICATION DIVISION.
       PROGRAM-ID. PAYROLL.
      *----------------------------------------------------------------
      * WEEKLY PAYROLL RUN.
      * COMPUTES GROSS PAY WITH OVERTIME, A DEPARTMENT BONUS, TAX AND
      * NET PAY FOR EVERY EMPLOYEE IN EMPLOYEES.DAT.
      *----------------------------------------------------------------
       ENVIRONMENT DIVISION.
       INPUT-OUTPUT SECTION.
       FILE-CONTROL.
           SELECT EMPLOYEE-FILE ASSIGN TO 'EMPLOYEES.DAT'
               ORGANIZATION IS LINE SEQUENTIAL
               FILE STATUS IS WS-EMP-STATUS.
           SELECT PAYSLIP-FILE ASSIGN TO 'PAYSLIPS.OUT'
               ORGANIZATION IS LINE SEQUENTIAL.

       DATA DIVISION.
       FILE SECTION.
       FD  EMPLOYEE-FILE.
       01  EMP-REC.
           05  EMP-ID               PIC 9(5).
           05  EMP-NAME             PIC X(10).
           05  EMP-DEPT             PIC 99.
           05  EMP-HOURS            PIC 9(3)V9.
           05  EMP-RATE             PIC 9(3)V99.
       FD  PAYSLIP-FILE.
       01  SLIP-REC.
           05  SLIP-ID              PIC 9(5).
           05  FILLER               PIC X.
           05  SLIP-NAME            PIC X(10).
           05  FILLER               PIC X.
           05  SLIP-DEPT            PIC X(3).
           05  FILLER               PIC X.
           05  SLIP-GROSS           PIC ZZ,ZZ9.99.
           05  FILLER               PIC X.
           05  SLIP-BONUS           PIC ZZ,ZZ9.99.
           05  FILLER               PIC X.
           05  SLIP-TAX             PIC ZZ,ZZ9.99.
           05  FILLER               PIC X.
           05  SLIP-NET             PIC ZZ,ZZ9.99.

       WORKING-STORAGE SECTION.
       01  WS-EMP-STATUS            PIC XX.
       01  WS-EOF                   PIC X VALUE 'N'.
       01  WS-ADMIN-PASSWORD        PIC X(8) VALUE 'PAY2020!'.
       01  WS-DEPT-DATA.
           05  FILLER               PIC X(9) VALUE '10ENG0500'.
           05  FILLER               PIC X(9) VALUE '20OPS0250'.
           05  FILLER               PIC X(9) VALUE '30SAL0750'.
       01  WS-DEPT-TABLE REDEFINES WS-DEPT-DATA.
           05  WS-DEPT-ENTRY OCCURS 3 TIMES.
               10  WS-DEPT-CODE     PIC 99.
               10  WS-DEPT-NAME     PIC X(3).
               10  WS-DEPT-BONUS    PIC 99V99.
       01  WS-IDX                   PIC 9 VALUE ZERO.
       01  WS-DEPT-LABEL            PIC X(3).
       01  WS-PAY-FIELDS.
           05  WS-REG-HOURS         PIC 9(3)V9.
           05  WS-OT-HOURS          PIC 9(3)V9.
           05  WS-GROSS             PIC 9(5)V99.
           05  WS-BONUS             PIC 9(5)V99.
           05  WS-TAXABLE           PIC 9(5)V99.
           05  WS-TAX               PIC 9(5)V99.
           05  WS-NET-PAY           PIC 9(5)V99.
       01  WS-TOTAL-NET             PIC 9(7)V99 COMP-3 VALUE ZERO.
       01  WS-TOTAL-DISP            PIC Z,ZZZ,ZZ9.99.
       01  WS-EMP-COUNT             PIC 9(4) VALUE ZERO.
       01  WS-OT-COUNT              PIC 9(4) VALUE ZERO.
       01  WS-REJECT-COUNT          PIC 9(4) VALUE ZERO.

       PROCEDURE DIVISION.
       0000-MAIN.
           PERFORM 1000-OPEN-FILES.
           PERFORM 2000-PROCESS-EMPLOYEE UNTIL WS-EOF = 'Y'.
           PERFORM 8000-PRINT-SUMMARY.
           PERFORM 9000-CLOSE-FILES.
           STOP RUN.

       1000-OPEN-FILES.
           OPEN INPUT EMPLOYEE-FILE
                OUTPUT PAYSLIP-FILE.
           MOVE SPACES TO SLIP-REC.
           PERFORM 1100-READ-EMPLOYEE.

       1100-READ-EMPLOYEE.
           READ EMPLOYEE-FILE
               AT END MOVE 'Y' TO WS-EOF
           END-READ.

       2000-PROCESS-EMPLOYEE.
           IF EMP-HOURS = ZERO OR EMP-RATE = ZERO
               DISPLAY 'REJECTED EMPLOYEE ' EMP-ID
               ADD 1 TO WS-REJECT-COUNT
           ELSE
               PERFORM 2100-COMPUTE-GROSS
               PERFORM 2200-FIND-DEPT-BONUS
               PERFORM 2300-COMPUTE-TAX
               COMPUTE WS-NET-PAY = WS-GROSS + WS-BONUS - WS-TAX
               PERFORM 2400-WRITE-PAYSLIP
           END-IF.
           PERFORM 1100-READ-EMPLOYEE.

       2100-COMPUTE-GROSS.
           IF EMP-HOURS > 40
               MOVE 40 TO WS-REG-HOURS
               COMPUTE WS-OT-HOURS = EMP-HOURS - 40
           ELSE
               MOVE EMP-HOURS TO WS-REG-HOURS
               MOVE ZERO TO WS-OT-HOURS
           END-IF.
           COMPUTE WS-GROSS ROUNDED =
               (WS-REG-HOURS * EMP-RATE) +
               (WS-OT-HOURS * EMP-RATE * 1.5).

       2200-FIND-DEPT-BONUS.
           MOVE ZERO TO WS-BONUS.
           MOVE '???' TO WS-DEPT-LABEL.
           PERFORM VARYING WS-IDX FROM 1 BY 1 UNTIL WS-IDX > 3
               IF EMP-DEPT = WS-DEPT-CODE (WS-IDX)
                   COMPUTE WS-BONUS ROUNDED =
                       WS-GROSS * WS-DEPT-BONUS (WS-IDX) / 100
                   MOVE WS-DEPT-NAME (WS-IDX) TO WS-DEPT-LABEL
               END-IF
           END-PERFORM.

       2300-COMPUTE-TAX.
           COMPUTE WS-TAXABLE = WS-GROSS + WS-BONUS.
           EVALUATE TRUE
               WHEN WS-TAXABLE <= 500
                   COMPUTE WS-TAX ROUNDED = WS-TAXABLE * 0.10
               WHEN WS-TAXABLE <= 1500
                   COMPUTE WS-TAX ROUNDED = WS-TAXABLE * 0.20
               WHEN OTHER
                   COMPUTE WS-TAX ROUNDED = WS-TAXABLE * 0.30
           END-EVALUATE.

       2400-WRITE-PAYSLIP.
           MOVE EMP-ID TO SLIP-ID.
           MOVE EMP-NAME TO SLIP-NAME.
           MOVE WS-DEPT-LABEL TO SLIP-DEPT.
           MOVE WS-GROSS TO SLIP-GROSS.
           MOVE WS-BONUS TO SLIP-BONUS.
           MOVE WS-TAX TO SLIP-TAX.
           MOVE WS-NET-PAY TO SLIP-NET.
           WRITE SLIP-REC.
           ADD 1 TO WS-EMP-COUNT.
           ADD WS-NET-PAY TO WS-TOTAL-NET.
           IF WS-OT-HOURS > ZERO
               ADD 1 TO WS-OT-COUNT
           END-IF.

       8000-PRINT-SUMMARY.
           MOVE WS-TOTAL-NET TO WS-TOTAL-DISP.
           DISPLAY 'EMPLOYEES PAID    : ' WS-EMP-COUNT.
           DISPLAY 'EMPLOYEES REJECTED: ' WS-REJECT-COUNT.
           DISPLAY 'WITH OVERTIME     : ' WS-OT-COUNT.
           DISPLAY 'TOTAL NET PAY     : ' WS-TOTAL-DISP.

       9000-CLOSE-FILES.
           CLOSE EMPLOYEE-FILE PAYSLIP-FILE.
