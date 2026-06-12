/**
 * Sheets bridge for the irrigation-only client routine.
 *
 * Deployed as a web app from the Schedule Master sheet (Extensions -> Apps
 * Script), executing as the sheet owner, access "Anyone". Every request must
 * carry the shared token. The Python tool (tools/irrigation_match) is the only
 * client.
 *
 * GET  ?token=...            -> JSON of all readable tabs from both sheets
 * POST ?token=...  body JSON -> writes to the Schedule Master only:
 *   {"replace": {"<tab>": [[...], ...]}, "append": {"<tab>": [[...], ...]}}
 *
 * TOKEN is replaced with a real value at deploy time; the repo copy keeps a
 * placeholder so no secret is committed.
 */

var TOKEN = 'REPLACE_WITH_TOKEN_AT_DEPLOY';
var SCHEDULE_MASTER_ID = '19CnRI2G-gOBJvCs6BFotJH_-n5FF06ebcUVopnPtqlo';
var IRRIGATION_ID = '15ElcgGzGVHRHb7gCl217HYh5AiwAb-5KTmC5e8oZa0g';

var READ_TABS = {
  schedule: ['Clients', 'Client Match Memory'],
  irrigation: ['Bozeman 26', 'Big sky 26', 'Remote clients 26'],
};
var WRITABLE_TABS = ['Irrigation Only Clients', 'Client Match Memory'];

function doGet(e) {
  if (!e.parameter || e.parameter.token !== TOKEN) {
    return jsonResponse({ error: 'bad token' });
  }
  var books = {
    schedule: SpreadsheetApp.openById(SCHEDULE_MASTER_ID),
    irrigation: SpreadsheetApp.openById(IRRIGATION_ID),
  };
  var payload = {};
  Object.keys(READ_TABS).forEach(function (book) {
    payload[book] = {
      _sheets: books[book].getSheets().map(function (sheet) {
        return sheet.getName();
      }),
    };
    READ_TABS[book].forEach(function (tab) {
      var sheet = findSheet(books[book], tab);
      payload[book][tab] = sheet ? sheet.getDataRange().getDisplayValues() : [];
    });
  });
  return jsonResponse(payload);
}

// Tab names on the live sheets carry stray apostrophes and spaces
// ("Bozeman '26", "Big sky 26'"); resolve ignoring case and punctuation.
function normalizeTabName(name) {
  return name.toLowerCase().replace(/[^a-z0-9]/g, '');
}

function findSheet(book, name) {
  var want = normalizeTabName(name);
  return (
    book.getSheets().filter(function (sheet) {
      return normalizeTabName(sheet.getName()) === want;
    })[0] || null
  );
}

function doPost(e) {
  if (!e.parameter || e.parameter.token !== TOKEN) {
    return jsonResponse({ error: 'bad token' });
  }
  var request = JSON.parse(e.postData.contents);
  var book = SpreadsheetApp.openById(SCHEDULE_MASTER_ID);
  var written = {};
  Object.keys(request.replace || {}).forEach(function (tab) {
    written[tab] = replaceTab(book, tab, request.replace[tab]);
  });
  Object.keys(request.append || {}).forEach(function (tab) {
    written[tab] = appendRows(book, tab, request.append[tab]);
  });
  return jsonResponse({ ok: true, written: written });
}

function replaceTab(book, tab, rows) {
  assertWritable(tab);
  var sheet = findSheet(book, tab) || book.insertSheet(tab);
  sheet.clearContents();
  if (rows.length) {
    sheet.getRange(1, 1, rows.length, rows[0].length).setValues(rows);
  }
  return 'replaced ' + rows.length + ' rows';
}

function appendRows(book, tab, rows) {
  assertWritable(tab);
  if (!rows.length) {
    return 'appended 0 rows';
  }
  var sheet = findSheet(book, tab) || book.insertSheet(tab);
  var startRow = sheet.getLastRow() + 1;
  sheet.getRange(startRow, 1, rows.length, rows[0].length).setValues(rows);
  return 'appended ' + rows.length + ' rows';
}

function assertWritable(tab) {
  if (WRITABLE_TABS.indexOf(tab) === -1) {
    throw new Error('tab not writable: ' + tab);
  }
}

function jsonResponse(data) {
  return ContentService.createTextOutput(JSON.stringify(data)).setMimeType(
    ContentService.MimeType.JSON
  );
}
