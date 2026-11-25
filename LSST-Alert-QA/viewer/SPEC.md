# Viewer specifications

The viewer shows the transient monitor's reports in a web page. It in turn serves as the test object for the Playwright suite. Each line is a rule, with examples if needed. Example files, made and real, are gathered in the tests/data/ sub folder.

Tests are written from these rules, not from the page’s code.

1. Report files are in reports/. Live report files have <name>s "transient_<FOOTPRINT>_<YYYYMMDD>_<hhmmss>.json".

2. Init report files have <name>s "transient_<FOOTPRINT>_<YYYYMMDD>_<hhmmss>_init.json".

3. Given reports/ is empty, when opened without ?file=, the page shows "Report folder empty" and no table.

4. Given reports/ can't be listed, when opened without ?file=, the page shows "Report folder not found" and no table.

5. Given ?file= containing / or \, the error page shows "Invalid file name". This rule applies before Rule 6.
   - Given ?file=transient_x/../../logs/replay_ecdfs_20261102_163727.json, then "Invalid file name".
   - Given ?file=transient_x\..\..\logs\replay_ecdfs_20261102_163727.json, then "Invalid file name".

6. Given ?file= matches neither Rule 1 nor Rule 2, the error page shows "Invalid file name: <name>".

7. Given opened without ?file=, the viewer must show the overview listing every report in reports/

8. Given reports/ has files, but none matches Rule 1 or 2, when opened without ?file=, the page shows "No reports found in the folder" and no table.

9. When a report file can't be opened, the error page shows "Report not found: <name>".

10. Given a file that isn't valid JSON, the error page shows "Not a valid report: <name>".
    - Given transient_foobar_20251003_000000.json containing {"mode":, then "Not a valid report: transient_foobar_20251003_000000.json"

11. Given a file is valid JSON, but contains no top-level run_utc, or run_utc is not a valid time, the error page shows "Not a valid report: <name>". Valid run_utc value defined as pattern: "<YYYY>-<MM>-<DD>T<hh>:<mm>:<ss>+00:00".
    - Given transient_foobar_20251001_000000.json containing "foobar", then "Not a valid report: transient_foobar_20251001_000000.json"
    - Given transient_foobar_20251002_000000.json containing {}, then "Not a valid report: transient_foobar_20251002_000000.json"
    - Given transient_foobar_20251009_000000.json containing {"run_utc": "2025-10-08T23:00:00+0100"}, then "Not a valid report: transient_foobar_20251009_000000.json"

12. When the page reads files, shows "Reading reports ..." line. The line must no longer be shown once table, a message or a report is there.

13. A ?file= page must be an error or report page. An error page belongs to Rules 5, 6 or 9–11, shows one of the messages of the rules. And a report page belongs to reports, shows the Report region (rule 17). This rule happens after Rule 12.

14. A ?file= page must have an "Overview" link; when a user clicks it, the overview shows, with "Reading reports ..." first if needed.

15. A report page contains a "Raw JSON" link to the file. An error page does not.
    - Given transient_ecdfs_20261105_160051.json, then shows the link.
    - Given transient_foobar_20251002_000000.json, then no Raw JSON link.

16. When a user clicks Raw JSON link, the file opens in a new tab.
    - Given transient_ecdfs_20261105_160051.json, the new tab’s address ends in reports/transient_ecdfs_20261105_160051.json
    - Given transient_<f>oobar_20251004_000000.json, the new tab’s address ends in reports/transient_%3Cf%3Eoobar_20251004_000000.json

17. A Report page contains one region "Report" showing its content. An error page shows no such region. The Raw JSON link isn't inside the Report region.
    - Given transient_ecdfs_20261109_160151.json, the page shows Report region.
    - Given transient_foobar_20251009_000000.json, the page does not show Report region.

18. In the overview, each file that can’t be opened or isn’t a report gets its message from Rules 9 to 11, above the table.

19. Given at least one good report file, the page lists the overview. Placed below the aforementioned bad file lines.

20. Given report files and none is good, when opened without ?file=, the page shows their message lines and no table.

21. Overview format. one report per row.

        Columns, in this order: Report, Date, Mode, Exit, Alerts, Reported Earlier, Message.

22. When clicks a row, the page opens that report.

23. Overview list sorted by Date by default, newest first

24. When reloads overview page or opens a new tab, overview starts with the default. Reloading report page doesn't change the sorting.

25. `Report` column shows the footprint

26. `Date` shows the run time in UTC.
    - Given transient_ecdfs_20261102_163727.json, Date must be "2026-11-02 15:37:27 UTC".

27. `Mode` shows mode: live or init

28. `Exit` shows exit_code

29. `Alerts` shows the number of entries in `alerts`
    - Given transient_ecdfs_20261110_160051.json, then Alerts is 6, not 7 (as is objects.kept)

30. Given Mode=init, Alerts shows "-".

31. `Reported Earlier` shows state_before.reported
    - Given transient_ecdfs_20261111_160243.json, then "8".

32. <SURVEY> names in `Message` are listing keys under listings, in uppercase.
    - Given listings.lsst, then "LSST".

33. Given exit 0, Message shows "-".
    - Given transient_ecdfs_20261110_160051.json, then "-".

34. Given exit 1, when one listing failed, `Message` shows "ERROR: <SURVEY> listing failed".
    - Given transient_ecdfs_20261102_163727.json, then "ERROR: LSST listing failed".
    - Given transient_ecdfs_20261112_224310.json, then "ERROR: ZTF listing failed".

35. Given exit 1, when two listings failed, Message shows "ERROR: LSST and ZTF listings failed".
    - Given transient_ecdfs_20261112_095402.json, then "ERROR: LSST and ZTF listings failed".
    - Given transient_ecdfs_20261112_095400_init.json, then "ERROR: LSST and ZTF listings failed".

36. Given exit 1, when no listing failed (including without listings at all), then the Message shows "ERROR: Network failed". Assumed: The monitor writes exit 1 without a failed listing only for network errors; a new cause needs a new line.
    - Given transient_ecdfs_20261112_095404.json, then "ERROR: Network failed".

37. Given exit_code missing, neither 0 nor 1, Message shows "ERROR: undefined error and exit code".
    - Given transient_ecdfs_20261122_220000.json contains {"exit_code": 2}, then "ERROR: undefined error and exit code".
    - Given transient_ecdfs_20261121_221344.json contains no exit_code, then "ERROR: undefined error and exit code".

38. Message is exactly one of the texts defined above (Rules 33 to 37), with nothing added.
    - Given transient_ecdfs_20261102_163727.json, then the Message is exactly "ERROR: LSST listing failed". as report's listings.lsst.detail contains "422", the Message must not.
    - Given transient_ecdfs_20261112_224310.json, then the Message is exactly "ERROR: ZTF listing failed". Report's listings.ztf.detail contains "alerce ztf listing page 1", the Message must not.

39. Sortable columns: Report, Date, Mode, Exit, and Alerts.

        Active column shows ▲ or ▼, the other sortable columns ↕.

        ▲ means the values ascend down the table (A→Z, 0→9, oldest→newest).

40. When click on sortable column headers, flip the order if sorted by it, else sort by it (Rules 43 to 48).

41. When click on other column headers, nothing happens.

42. Given there is a second sort order, flip does not flip the second sort.

43. When sorted by Date, newest first. Assumed: No same run_utc.

44. All except Date must be sorted by Date secondly, newest first.

45. When sorted by Report, in alphabetical order

46. When sorted by Exit, in descend order, non zero first.

47. When Sorted by Mode, init first.

48. When sorted by Alerts, biggest number first.

49. "-" last in both directions

50. A cell whose value is missing from the report shows "-".
    - Given transient_ecdfs_20261121_221344.json, then Exit and Reported Earlier show "-".

51. Given user is in report page, when returns to overview by the link, or by Back, overview keeps the sort.

52. Text from URL must appear character for character.
    - Given ?file=<foo>bar.json, then "Invalid file name: <foo>bar.json".
    - Given ?file=foo+bar.json, then "Invalid file name: foo+bar.json".

53. Text from a report must appear character for character.
    - Given transient_<f>oobar_20251004_000000.json whose "footprint" is "<f>oobar", then "<f>oobar" shows in the Report column of the overview page, and in the main heading of the report page.

54. In the Report region, the main heading is its run_utc, footprint, and mode. Assumed: A report with run_utc also has footprint and mode.
    - Given ?file=transient_ecdfs_20261105_160051.json, then "2026-11-05 15:00:51 UTC - ecdfs - live".
    - Given ?file=transient_ecdfs_20261112_095400_init.json, then "2026-11-12 08:54:00 UTC - ecdfs - init".

55. Cone line in the Report region. Labels: "Cone", "RA", "Dec", "Radius". Values: ra=cone.0, dec=cone.1, r=cone.2. It shows "Cone" "RA <ra>°" "Dec <dec>°" "Radius <r>°". The line shows these four texts, in this order; what separates them is free. Values as written in the report; whole number shows with ".0". Also, the minus is the plain keyboard hyphen. Assumed: Report writes cone values as decimal numbers (90.0, never 90).
    - Given ?file=transient_ecdfs_20261111_160243.json, then "Cone" "RA 53.1°" "Dec -27.8°" "Radius 2.82°".
    - Given ?file=transient_<f>oobar_20251004_000000.json, then "Cone" "RA 200.5°" "Dec 90.0°" "Radius 1.25°".

56. Top level keys: None must be missed
   - mode
   - footprint
   - cone
   - run_utc
   - run_mjd
   - since_mjd
   - settings
   - state_before
   - listings
   - photometry
   - fetch_errors
   - objects
   - rejected
   - rapid_rise_vetoed (new)
   - parked
   - alerts, or recorded by init
   - tns_earlier
   - point_source_watch
   - warnings
   - exit_code

57. When parked.new has more than 10 entries, it is collapsed under a line with its count ("192 newly parked"); a click expands all entries, another collapses them.

58. Listing status per survey. "LSST quiet since 2026-11-07"
