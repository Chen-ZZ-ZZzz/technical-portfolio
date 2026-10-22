*** Settings ***
Documentation    Black-box checks for systemd units of the checker journal scanner
Library          Process
Test Tags        live

*** Variables ***
${SCANNER}    checker-journal-scan
@{JOURNAL}    journalctl    --user    -u    ${SCANNER}    --no-pager
@{LAST}       --since       -66min
@{SYSCTL}     systemctl     --user

*** Test Cases ***
Timer is active
    Unit Should Be Active    ${SCANNER}.timer

Ran within the last hour
    ${result} =       Run Journal         @{LAST}
    Should Contain    ${result.stdout}    Finished

Last run succeeded
    ${value} =         Unit Property    ${SCANNER}.service    Result
    Should Be Equal    ${value}         success

*** Keywords ***
Run Journal
    [Arguments]               @{args}
    Run Keyword And Return    Run Process    @{JOURNAL}    @{args}

Run Systemctl
    [Arguments]               @{args}
    Run Keyword And Return    Run Process    @{SYSCTL}    @{args}

Unit Should Be Loaded
    [Documentation]    Check if a unit is "loaded".
    [Arguments]        ${name}
    ${result} =        Run Systemctl       show    ${name}    -p    LoadState    --value
    Should Be Equal    ${result.stdout}    loaded
    Should Be Equal    ${result.rc}        0       type=int

Unit Should Be Active
    [Arguments]              ${name}
    Unit Should Be Loaded    ${name}
    ${result} =              Run Systemctl       is-active    ${name}
    Should Be Equal          ${result.stdout}    active
    Should Be Equal          ${result.rc}        0            type=int

Unit Property
    [Arguments]              ${name}          ${property}
    Unit Should Be Loaded    ${name}
    ${result} =              Run Systemctl    show    ${name}    -p    ${property}    --value
    Should Be Equal          ${result.rc}     0       type=int
    RETURN                   ${result.stdout}
