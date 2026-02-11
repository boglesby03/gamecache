const initSqlJs = require('./sql-wasm.js');

initSqlJs({
  locateFile: filename => './sql-wasm.wasm'
}).then(SQL => {
  const db = new SQL.Database();
  try {
    db.run("CREATE VIRTUAL TABLE test USING fts5(content);");
    console.log("FTS5 is enabled! 🎉");
  } catch (e) {
    console.error("FTS5 not enabled:", e.message);
  }
});
