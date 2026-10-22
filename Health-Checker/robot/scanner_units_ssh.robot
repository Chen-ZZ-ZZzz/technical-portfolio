*** Settings ***
Documentation     An example that demonstrates executing commands on a remote
...               machine. This is an SSH version of the units tests for the
...               checker journal scanner. The keyword `Execute Command` from
...               SSHLibrary is called in the lowest wrappers because it takes
...               the whole command as one string and returns stdout, stderr and
...               rc as separate values, depending on its flags.
Library           SSHLibrary
Test Tags         live
Suite Setup       Open Connection And Log In
Suite Teardown    Close All Connections

*** Variables ***
${HOST}        localhost
${SSH_USER}    %{USER}
${SSH_KEY}     %{HOME}/.ssh/robot_localhost
${SCANNER}     checker-journal-scan
@{SYSCTL}      systemctl     --user
@{JOURNAL}     journalctl    --user    -u    ${SCANNER}    --no-pager
@{LAST}        --since       -66min

*** Test Cases ***
Timer is active
    Unit Should Be Active    ${SCANNER}.timer

Last run succeeded
    ${value} =         Unit Property    ${SCANNER}.service    Result
    Should Be Equal    ${value}         success

Ran within the last hour
    ${out}             ${rc} =    Run Journal    @{LAST}
    Should Contain     ${out}     Finished
    Should Be Equal    ${rc}      0              type=int

*** Keywords ***
Open Connection And Log In
    Open Connection          ${HOST}
    Login With Public Key    ${SSH_USER}    ${SSH_KEY}

Run Systemctl
    [Arguments]               @{args}
    ${cmd} =                  Set Variable          ${{shlex.join([*$SYSCTL, *$args])}}
    Run Keyword And Return    Execute Command       ${cmd}
    ...                       return_stdout=True    return_rc=True

Unit Should Be Loaded
    [Documentation]    Check if a unit is "loaded".
    [Arguments]        ${name}
    ${out}             ${rc} =    Run Systemctl    show    ${name}    -p    LoadState    --value
    Should Be Equal    ${out}     loaded
    Should Be Equal    ${rc}      0                type=int

Unit Should Be Active
    [Arguments]              ${name}
    Unit Should Be Loaded    ${name}
    ${out}                   ${rc} =    Run Systemctl    is-active    ${name}
    Should Be Equal          ${out}     active
    Should Be Equal          ${rc}      0                type=int

Unit Property
    [Arguments]              ${name}    ${property}
    Unit Should Be Loaded    ${name}
    ${out}                   ${rc} =    Run Systemctl    show    ${name}    -p    ${property}    --value
    Should Be Equal          ${rc}      0                type=int
    RETURN                   ${out}

Run Journal
    [Arguments]               @{args}
    ${cmd} =                  Set Variable          ${{shlex.join([*$JOURNAL, *$args])}}
    Run Keyword And Return    Execute Command       ${cmd}
    ...                       return_stdout=True    return_rc=True
