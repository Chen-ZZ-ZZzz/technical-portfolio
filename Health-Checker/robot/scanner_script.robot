*** Settings ***
Documentation    Testing bad inputs to the script wrapper of checker journal
...              scanner. The first test puts in an invalid "SCAN_DELAY" value.
...              The second test points to a garbage cursor. The third tests
...              the script taking no arguments. Tests are run in a temporary
...              directory. The script itself runs as a copy there. Live state
...              files are not written.
...
...              The script wrapper would check the existence of reports/ dir
...              next to the script, make one if it isn't there. None of these
...              runs get that far. Actual report writing is handled in the
...              python module code.
Library          OperatingSystem
Library          Process
Test Setup       Make Temps
Test Teardown    Remove Directory    ${TMP}    recursive=True
Test Timeout     60 seconds

*** Variables ***
${SCAN_WRAPPER} =    ${CURDIR}/../journal_scan.sh

*** Test Cases ***
Invalid Sleep Delay
    ${result} =                   Run Scan Wrapper    abc
    Should Contain                ${result.stderr}    sleep: invalid time interval
    Should Be Equal               ${result.rc}        ${1}
    Directory Should Be Empty     ${TEMPSTATE}
    Directory Should Not Exist    ${TMP}/reports

Invalid Cursor
    Create File                   ${TEMPSTATE}/cursor    nonsense
    ${result} =                   Run Scan Wrapper       0
    Should Contain                ${result.stderr}       Failed to seek to cursor
    Should Be Equal               ${result.rc}           ${1}
    ${cursor} =                   Get File               ${TEMPSTATE}/cursor
    Should Be Equal               ${cursor}              nonsense
    Directory Should Not Exist    ${TMP}/reports

Extra Argument
    ${result} =                   Run Scan Wrapper    bar    foo
    Should Contain                ${result.stderr}    takes no arguments
    Should Not Contain            ${result.stderr}    invalid time interval
    Should Be Equal               ${result.rc}        ${1}
    Directory Should Be Empty     ${TEMPSTATE}
    Directory Should Not Exist    ${TMP}/reports

*** Keywords ***
Make Temps
    VAR                 ${TMP}             ${{tempfile.mkdtemp()}}    scope=TEST
    VAR                 ${TEMPSTATE}       ${TMP}/state               scope=TEST
    Create Directory    ${TEMPSTATE}
    Copy File           ${SCAN_WRAPPER}    ${TMP}/

Run Scan Wrapper
    [Arguments]    ${delay}    @{args}
    ${result} =    Run Process                bash    ${TMP}/journal_scan.sh    @{args}
    ...            env:SCAN_DELAY=${delay}    env:STATE_DIRECTORY=${TEMPSTATE}
    ...            timeout=10s                env:LC_ALL=C
    RETURN         ${result}
