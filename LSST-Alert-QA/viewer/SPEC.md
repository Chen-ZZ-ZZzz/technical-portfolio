# Viewer specifications

The viewer shows the transient monitor's reports in a web page. It in turn serves as the test object for the Playwright suite. Each line is a rule, with examples if needed. Example files, made and real, are gathered in the tests/data/ sub folder.

Tests are written from these rules, not from the page’s code.

1. Report files are in reports/. Live report files have <name>s "transient_<FOOTPRINT>_<YYYYMMDD>_<HHMMSS>.json".
2. Init report files have <name>s "transient_<FOOTPRINT>_<YYYYMMDD>_<HHMMSS>_init.json".
3. Given reports/ is empty, when opened without ?file=, the page shows "Report folder empty" and no table.
4. Given ?file= containing / or \, the page shows "Invalid file name" and no report. This rule applies before Rule 5.
   Given ?file=transient_x/../../logs/replay_ecdfs_20261102_163727.json, then "Invalid file name".
   Given ?file=transient_x\..\..\logs\replay_ecdfs_20261102_163727.json, then "Invalid file name".
5. Given ?file= matches neither Rule 1 nor Rule 2, the page shows "Invalid file name: <name>" and no report.
6. Given opened without ?file=, the viewer must show the overview listing every report in reports/
7. Given reports/ has files, but none matches Rule 1 or 2, when opened without ?file=, the page shows "No reports found in the folder" and no table.
8. When the page reads files, shows "Reading reports ..." line. The line must no longer be shown once table, a message or a report is there.
9. When the page shows the report, the main heading is its run_utc, footprint, and mode. Assumed: A report with run_utc also has footprint and mode.
   Given ?file=transient_ecdfs_20261105_160051.json, then "2026-11-05 15:00:51 UTC - ecdfs - live".
   Given ?file=transient_ecdfs_20261112_095400_init.json, then "2026-11-12 08:54:00 UTC - ecdfs - init".
10. When a report file can't be opened, the page shows "Report not found: <name>" and no report.
11. Given a file that isn't valid JSON, the page shows "Not a valid report: <name>".
    Given transient_foobar_20251003_000000.json containing {"mode":, then "Not a valid report: transient_foobar_20251003_000000.json"
12. Given a file is valid JSON, but contains no top-level run_utc, the page shows "Not a valid report: <name>".
    Given transient_foobar_20251001_000000.json containing "foobar", then "Not a valid report: transient_foobar_20251001_000000.json"
    Given transient_foobar_20251002_000000.json containing {}, then "Not a valid report: transient_foobar_20251002_000000.json"
13. In the overview, each file that can’t be opened or isn’t a report gets its message from Rules 10 to 12, above the table.
14. Given at least one good report file, the page lists the overview. Placed below the aforementioned bad file lines.
15. Given report files and none is good, when opened without ?file=, the page shows their message lines and no table.
16. Overview format. one report per row.
    Columns, in this order: Report, Date, Mode, Exit, Alerts, Reported Earlier, Message.
17. When clicks a row, the page opens that report.
18. The report page has an "Overview" link; when a user clicks it, the overview shows, with "Reading reports ..." first if needed.
19. Overview list sorted by Date by default, newest first
20. When reloads or opens a new tab, overview starts with the default.
21. `Report` column shows the footprint
22. `Date` shows the run time in UTC.
    Given transient_ecdfs_20261102_163727.json, Date must be "2026-11-02 15:37:27 UTC".
23. `Mode` shows mode: live or init
24. `Exit` shows exit_code
25. `Alerts` shows the number of entries in `alerts`
    Given transient_ecdfs_20261110_160051.json, then Alerts is 6, not 7 (as is objects.kept)
26. Given Mode=init, Alerts shows "-".
27. `Reported Earlier` shows state_before.reported
    Given transient_ecdfs_20261111_160243.json, then "8".
28. <SURVEY> names in `Message` are listing keys under listings, in uppercase.
    Given listings.lsst, then "LSST".
29. Given exit 0, Message shows "-".
    Given transient_ecdfs_20261110_160051.json, then "-".
30. Given exit 1, when one listing failed, `Message` shows "ERROR: <SURVEY> listing failed".
    Given transient_ecdfs_20261102_163727.json, then "ERROR: LSST listing failed".
    Given transient_ecdfs_20261112_224310.json, then "ERROR: ZTF listing failed".
31. Given exit 1, when two listings failed, Message shows "ERROR: LSST and ZTF listings failed".
    Given transient_ecdfs_20261112_095402.json, then "ERROR: LSST and ZTF listings failed".
    Given transient_ecdfs_20261112_095400_init.json, then "ERROR: LSST and ZTF listings failed".
32. Given exit 1, when no listing failed (including without listings at all), then the Message shows "ERROR: Network failed". Assumed: The monitor writes exit 1 without a failed listing only for network errors; a new cause needs a new line.
    Given transient_ecdfs_20261112_095404.json, then "ERROR: Network failed".
33. Message is exactly one of the texts defined above (Rules 29 to 32), with nothing added.
    Given transient_ecdfs_20261102_163727.json, then the Message is exactly "ERROR: LSST listing failed". as report's listings.lsst.detail contains "422", the Message must not.
    Given transient_ecdfs_20261112_224310.json, then the Message is exactly "ERROR: ZTF listing failed". Report's listings.ztf.detail contains "alerce ztf listing page 1", the Message must not.
34. Sortable columns: Report, Date, Mode, Exit, and Alerts. Active column shows ▲ or ▼, the others ↕.
    ▲ means the values ascend down the table (A→Z, 0→9, oldest→newest).
35. When click on sortable column headers, flip the order if sorted by it, else sort by it (Rules 38 to 43).
36. When click on other column headers, nothing happens.
37. Given there is a second sort order, flip does not flip the second sort.
38. When sorted by Date, newest first. Assumed: No same run_utc.
39. All except Date must be sorted by Date secondly, newest first.
40. When sorted by Report, in alphabetical order
41. When sorted by Exit, in descend order, non zero first.
42. When Sorted by Mode, init first.
43. When sorted by Alerts, biggest number first.
44. "-" last in both directions
45. A cell whose value is missing from the report shows "-".
46. Given user is in report page, when returns to overview by the link, or by Back, overview keeps the sort.
47. Text from URL must appear character for character.
    Given ?file=<foo>bar.json, then "Invalid file name: <foo>bar.json".
48. Text from a report must appear character for character.
    Given transient_<f>oobar_20251004_000000.json whose "footprint" is "<f>oobar", then "<f>oobar" shows in the Report column of the overview page, and in the main heading of the report page.
49. On the report page, there is a link to the report being shown. When a user clicks it, the file opens in a new tab. The link text: "Raw JSON"
    Given ?file=transient_ecdfs_20261105_160051.json, the new tab’s address ends in reports/transient_ecdfs_20261105_160051.json
    Given ?file=transient_<f>oobar_20251004_000000.json, the new tab’s address ends in reports/transient_%3Cf%3Eoobar_20251004_000000.json
50. Cone line on the report page. Labels: "Cone", "RA", "Dec", "Radius". Values: ra=cone.0, dec=cone.1, r=cone.2. It shows "Cone" "RA <ra>°" "Dec <dec>°" "Radius <r>°". The line shows these four texts, in this order; what separates them is free. Values as written in the report; whole number shows with ".0". Also, the minus is the plain keyboard hyphen. Assumed: Report writes cone values as decimal numbers (90.0, never 90).
    Given ?file=transient_ecdfs_20261111_160243.json, then "Cone" "RA 53.1°" "Dec -27.8°" "Radius 2.82°".
    Given ?file=transient_<f>oobar_20251004_000000.json, then "Cone" "RA 200.5°" "Dec 90.0°" "Radius 1.25°".
51. Top level keys: None must be missed
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
   - parked
   - alerts, or recorded by init
   - tns_earlier
   - point_source_watch
   - warnings
   - exit_code

53. When parked.new has more than 10 entries, it is collapsed under a line with its count ("192 newly parked"); a click expands all entries, another collapses them.
54. Listing status per survey. "LSST quiet since 2026-11-07"
