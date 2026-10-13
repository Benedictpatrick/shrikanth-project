       IDENTIFICATION DIVISION.
       PROGRAM-ID. INTCALC.
      *----------------------------------------------------------------
      * MONTHLY INTEREST POSTING FOR THE ACCOUNT MASTER FILE.
      * READS ACCOUNTS.DAT, POSTS INTEREST OR AN OVERDRAFT FEE TO EACH
      * ACCOUNT AND WRITES THE RESULT TO POSTED.DAT.
      *----------------------------------------------------------------
       ENVIRONMENT DIVISION.
       INPUT-OUTPUT SECTION.
       FILE-CONTROL.
           SELECT ACCOUNT-FILE ASSIGN TO 'ACCOUNTS.DAT'
               ORGANIZATION IS LINE SEQUENTIAL
               FILE STATUS IS WS-IN-STATUS.
           SELECT POSTED-FILE ASSIGN TO 'POSTED.DAT'
               ORGANIZATION IS LINE SEQUENTIAL
               FILE STATUS IS WS-OUT-STATUS.

       DATA DIVISION.
       FILE SECTION.
       FD  ACCOUNT-FILE.
           COPY ACCTREC.
       FD  POSTED-FILE.
       01  POSTED-REC.
           05  POST-ID              PIC 9(6).
           05  FILLER               PIC X.
           05  POST-NAME            PIC X(12).
           05  FILLER               PIC X.
           05  POST-INTEREST        PIC ZZZ,ZZ9.99-.
           05  FILLER               PIC X.
           05  POST-BALANCE         PIC Z,ZZZ,ZZ9.99-.

       WORKING-STORAGE SECTION.
       01  WS-IN-STATUS             PIC XX.
       01  WS-OUT-STATUS            PIC XX.
       01  WS-EOF-FLAG              PIC X VALUE 'N'.
           88  END-OF-ACCOUNTS      VALUE 'Y'.
       01  WS-RATE                  PIC 9V9999 VALUE ZERO.
       01  WS-INTEREST              PIC S9(7)V99 VALUE ZERO.
       01  WS-FEE                   PIC 9(3)V99 VALUE 5.00.
       01  WS-COUNTERS.
           05  WS-READ-COUNT        PIC 9(5) VALUE ZERO.
           05  WS-POSTED-COUNT      PIC 9(5) VALUE ZERO.
           05  WS-SKIPPED-COUNT     PIC 9(5) VALUE ZERO.
       01  WS-TOTAL-INTEREST        PIC S9(9)V99 COMP-3 VALUE ZERO.
       01  WS-TOTAL-DISP            PIC Z,ZZZ,ZZ9.99-.

       PROCEDURE DIVISION.
       0000-MAIN.
           PERFORM 1000-INITIALIZE.
           PERFORM 2000-PROCESS-ACCOUNT UNTIL END-OF-ACCOUNTS.
           PERFORM 3000-FINALIZE.
           STOP RUN.

       1000-INITIALIZE.
           OPEN INPUT ACCOUNT-FILE
                OUTPUT POSTED-FILE.
           MOVE SPACES TO POSTED-REC.
           PERFORM 2100-READ-ACCOUNT.

       2000-PROCESS-ACCOUNT.
           ADD 1 TO WS-READ-COUNT.
           EVALUATE TRUE
               WHEN ACCT-BALANCE < ZERO
                   PERFORM 2300-CHARGE-OVERDRAFT-FEE
               WHEN ACCT-SAVINGS OR ACCT-CHECKING OR ACCT-PREMIUM
                   PERFORM 2200-DETERMINE-RATE
                   COMPUTE WS-INTEREST ROUNDED =
                       ACCT-BALANCE * WS-RATE / 12
                   PERFORM 2400-POST-INTEREST
               WHEN OTHER
                   DISPLAY 'UNKNOWN ACCOUNT TYPE FOR ' ACCT-ID
                   ADD 1 TO WS-SKIPPED-COUNT
           END-EVALUATE.
           PERFORM 2100-READ-ACCOUNT.

       2100-READ-ACCOUNT.
           READ ACCOUNT-FILE
               AT END SET END-OF-ACCOUNTS TO TRUE
           END-READ.

       2200-DETERMINE-RATE.
           MOVE ZERO TO WS-RATE.
           EVALUATE TRUE
               WHEN ACCT-SAVINGS
                   MOVE 0.0250 TO WS-RATE
               WHEN ACCT-CHECKING
                   IF ACCT-BALANCE >= 1000
                       MOVE 0.0050 TO WS-RATE
                   END-IF
               WHEN ACCT-PREMIUM
                   IF ACCT-BALANCE > 10000
                       MOVE 0.0400 TO WS-RATE
                   ELSE
                       MOVE 0.0300 TO WS-RATE
                   END-IF
           END-EVALUATE.

       2300-CHARGE-OVERDRAFT-FEE.
           COMPUTE WS-INTEREST = 0 - WS-FEE.
           PERFORM 2400-POST-INTEREST.

       2400-POST-INTEREST.
           ADD WS-INTEREST TO ACCT-BALANCE.
           ADD WS-INTEREST TO WS-TOTAL-INTEREST.
           MOVE ACCT-ID TO POST-ID.
           MOVE ACCT-NAME TO POST-NAME.
           MOVE WS-INTEREST TO POST-INTEREST.
           MOVE ACCT-BALANCE TO POST-BALANCE.
           WRITE POSTED-REC.
           ADD 1 TO WS-POSTED-COUNT.

       3000-FINALIZE.
           CLOSE ACCOUNT-FILE POSTED-FILE.
           MOVE WS-TOTAL-INTEREST TO WS-TOTAL-DISP.
           DISPLAY 'ACCOUNTS READ     : ' WS-READ-COUNT.
           DISPLAY 'INTEREST POSTED   : ' WS-POSTED-COUNT.
           DISPLAY 'ACCOUNTS SKIPPED  : ' WS-SKIPPED-COUNT.
           DISPLAY 'TOTAL INTEREST    : ' WS-TOTAL-DISP.
