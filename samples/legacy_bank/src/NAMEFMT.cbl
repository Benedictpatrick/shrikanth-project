       IDENTIFICATION DIVISION.
       PROGRAM-ID. NAMEFMT.
      *----------------------------------------------------------------
      * CUSTOMER NAME FORMATTER FOR STATEMENT PRINTING.
      * CONVERTS "FIRST LAST" RECORDS TO "LAST, FIRST" IN UPPER CASE.
      * USES STRING AND INSPECT, WHICH THE RULE-BASED TRANSLATOR DOES
      * NOT HANDLE - THIS PROGRAM EXERCISES THE LLM TRANSLATION PATH.
      *----------------------------------------------------------------
       ENVIRONMENT DIVISION.
       INPUT-OUTPUT SECTION.
       FILE-CONTROL.
           SELECT NAME-IN ASSIGN TO 'NAMES.DAT'
               ORGANIZATION IS LINE SEQUENTIAL
               FILE STATUS IS WS-IN-STATUS.
           SELECT NAME-OUT ASSIGN TO 'FORMATTED.DAT'
               ORGANIZATION IS LINE SEQUENTIAL
               FILE STATUS IS WS-OUT-STATUS.

       DATA DIVISION.
       FILE SECTION.
       FD  NAME-IN.
       01  NAME-IN-REC.
           05  IN-FIRST             PIC X(10).
           05  IN-LAST              PIC X(12).
       FD  NAME-OUT.
       01  NAME-OUT-REC             PIC X(24).

       WORKING-STORAGE SECTION.
       01  WS-IN-STATUS             PIC XX.
       01  WS-OUT-STATUS            PIC XX.
       01  WS-EOF                   PIC X VALUE 'N'.
       01  WS-FORMATTED             PIC X(24).
       01  WS-COUNT                 PIC 9(4) VALUE ZERO.

       PROCEDURE DIVISION.
       0000-MAIN.
           OPEN INPUT NAME-IN
                OUTPUT NAME-OUT.
           PERFORM UNTIL WS-EOF = 'Y'
               READ NAME-IN
                   AT END MOVE 'Y' TO WS-EOF
                   NOT AT END PERFORM 1000-FORMAT-NAME
               END-READ
           END-PERFORM.
           CLOSE NAME-IN NAME-OUT.
           DISPLAY 'NAMES FORMATTED: ' WS-COUNT.
           STOP RUN.

       1000-FORMAT-NAME.
           MOVE SPACES TO WS-FORMATTED.
           STRING IN-LAST DELIMITED BY SPACE
                  ', ' DELIMITED BY SIZE
                  IN-FIRST DELIMITED BY SPACE
                  INTO WS-FORMATTED
           END-STRING.
           INSPECT WS-FORMATTED CONVERTING
               'abcdefghijklmnopqrstuvwxyz' TO
               'ABCDEFGHIJKLMNOPQRSTUVWXYZ'.
           MOVE WS-FORMATTED TO NAME-OUT-REC.
           WRITE NAME-OUT-REC.
           ADD 1 TO WS-COUNT.
