      * ACCOUNT MASTER RECORD LAYOUT - SHARED BY THE NIGHTLY BATCH JOBS
       01  ACCT-REC.
           05  ACCT-ID              PIC 9(6).
           05  ACCT-NAME            PIC X(12).
           05  ACCT-TYPE            PIC X.
               88  ACCT-SAVINGS     VALUE 'S'.
               88  ACCT-CHECKING    VALUE 'C'.
               88  ACCT-PREMIUM     VALUE 'P'.
           05  ACCT-BALANCE         PIC S9(7)V99.
