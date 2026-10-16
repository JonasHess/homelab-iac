(function () {
  "use strict";

  const fileInput = document.getElementById("nfcFile");
  const createButton = document.getElementById("createJson");
  const details = document.getElementById("dumpDetails");
  const status = document.getElementById("status");
  let selectedDump = null;

  function setStatus(message, kind) {
    status.textContent = message;
    status.dataset.kind = kind || "";
  }

  function parseDump(text) {
    const values = new Map();
    for (const line of text.replace(/^\uFEFF/, "").split(/\r?\n/)) {
      const separator = line.indexOf(":");
      if (separator < 0) continue;
      const key = line.slice(0, separator).trim();
      if (key === "UID" || key === "Data Content") {
        values.set(key, line.slice(separator + 1).trim());
      }
    }

    const uid = values.get("UID");
    const auth = values.get("Data Content");
    if (!uid || !auth) {
      throw new Error("The file must contain UID and Data Content fields.");
    }

    const uidBytes = uid.split(/\s+/);
    if (
      uidBytes.length !== 8 ||
      uidBytes.some((byte) => !/^[\da-f]{2}$/i.test(byte))
    ) {
      throw new Error("The UID must consist of exactly 8 hexadecimal bytes.");
    }

    const cloudAuth = auth.replace(/\s+/g, "").toUpperCase();
    if (!/^[\da-f]{64}$/i.test(cloudAuth)) {
      throw new Error(
        "Data Content must contain exactly 32 authentication bytes (64 hexadecimal characters)."
      );
    }

    const ruid = uidBytes.reverse().join("").toUpperCase();
    return {
      ruid,
      cloudAuth,
      folder: ruid.slice(0, 8),
      file: ruid.slice(8),
    };
  }

  function contentPath(folder, file, extension) {
    return [folder, `${file}.${extension}`]
      .map((part) => encodeURIComponent(part))
      .join("/");
  }

  async function listContentFiles(path) {
    const response = await fetch(
      `/api/fileIndex?special=content&path=${encodeURIComponent(path)}`,
      { cache: "no-store" }
    );
    if (!response.ok) {
      throw new Error(
        `Could not read the content directory ${path} (HTTP ${response.status}).`
      );
    }
    const index = await response.json();
    return Array.isArray(index.files) ? index.files : [];
  }

  async function contentFileExists(folder, name) {
    const rootFiles = await listContentFiles("/");
    if (!rootFiles.some((item) => item.name === folder && item.isDirectory)) {
      return false;
    }
    const files = await listContentFiles(`/${folder}`);
    return files.some((item) => item.name === name && !item.isDirectory);
  }

  async function ensureContentFolder(folder) {
    const entry = (await listContentFiles("/")).find(
      (item) => item.name === folder
    );
    if (entry && entry.isDirectory) return;
    if (entry) {
      throw new Error(`The target path ${folder} is not a content directory.`);
    }

    const response = await fetch("/api/dirCreate?special=content", {
      method: "POST",
      headers: { "Content-Type": "text/plain;charset=UTF-8" },
      body: `/${folder}`,
    });
    if (!response.ok) {
      const detail = (await response.text()).trim();
      throw new Error(
        `Could not create the content directory (HTTP ${response.status})${detail ? `: ${detail}` : "."}`
      );
    }
  }

  async function uploadJson(dump) {
    await ensureContentFolder(dump.folder);

    const json = {
      live: false,
      nocloud: false,
      source: "",
      skip_seconds: 0,
      cache: false,
      cloud_ruid: dump.ruid,
      cloud_auth: dump.cloudAuth,
      cloud_override: false,
      tonie_model: "",
      hide: false,
      claimed: false,
      _version: 5,
    };
    const jsonFile = new File(
      [JSON.stringify(json, null, 4)],
      `${dump.file}.json`,
      { type: "application/json" }
    );
    const formData = new FormData();
    formData.append(jsonFile.name, jsonFile);

    const endpoint =
      `/api/fileUpload?special=content&path=${encodeURIComponent(dump.folder)}`;
    const response = await fetch(endpoint, {
      method: "POST",
      body: formData,
    });
    if (!response.ok) {
      throw new Error(
        `JSON upload failed (HTTP ${response.status} ${response.statusText}).`
      );
    }

    return contentPath(dump.folder, dump.file, "json");
  }

  const CACHE_POLL_INTERVAL_MS = 2000;
  const CACHE_POLL_TIMEOUT_MS = 10 * 60 * 1000;

  // Starts the Boxine download in TeddyCloud and hangs up as soon as the
  // response headers arrive. TeddyCloud keeps reading from Boxine and caching
  // after its client disconnects, so the TAF never has to pass through the
  // browser, where a reverse proxy may cut a long transfer. The cost: an
  // error that Boxine reports mid-stream is not seen here, only as a cache
  // that never shows up (see waitForCachedTaf).
  async function startTafCaching(dump) {
    const jsonPath = contentPath(dump.folder, dump.file, "json");
    const controller = new AbortController();
    const response = await fetch(`/content/download/${jsonPath}`, {
      signal: controller.signal,
    });
    controller.abort();
    if (!response.ok) {
      throw new Error(
        `Boxine retrieval failed (HTTP ${response.status} ${response.statusText}).`
      );
    }
  }

  // TeddyCloud writes the download to "<file>.tmp" and renames it to
  // "<file>" only once it is a valid TAF. With cloud.cacheToLibrary it then
  // moves the TAF into the library and points the content JSON's "source"
  // at it. Either outcome means the TAF is complete. Errors count as "not yet"
  // so that one failed poll does not end the wait.
  async function isTafCached(dump) {
    try {
      const jsonResponse = await fetch(`/content/json/get/${dump.ruid}`, {
        cache: "no-store",
      });
      if (jsonResponse.ok) {
        const json = await jsonResponse.json();
        if (json.source) return true;
      }
      return await contentFileExists(dump.folder, dump.file);
    } catch (_) {
      return false;
    }
  }

  async function waitForCachedTaf(dump) {
    const startedAt = Date.now();
    while (Date.now() - startedAt < CACHE_POLL_TIMEOUT_MS) {
      if (await isTafCached(dump)) {
        setStatus(
          "JSON created and TAF retrieved from Boxine. TeddyCloud has stored the TAF file.",
          "success"
        );
        return;
      }
      const seconds = Math.round((Date.now() - startedAt) / 1000);
      setStatus(
        `TeddyCloud is retrieving the TAF file from Boxine ... (${seconds} s)`,
        ""
      );
      await new Promise((resolve) => setTimeout(resolve, CACHE_POLL_INTERVAL_MS));
    }
    throw new Error(
      "TeddyCloud did not store the TAF file within 10 minutes. Check that cloud.enabled and cloud.cacheContent are on, and look at the TeddyCloud log."
    );
  }

  fileInput.addEventListener("change", async () => {
    selectedDump = null;
    createButton.disabled = true;
    details.hidden = true;

    const file = fileInput.files && fileInput.files[0];
    if (!file) {
      setStatus("", "");
      return;
    }

    if (!file.name.toLowerCase().endsWith(".nfc")) {
      setStatus("Please select a file with the .nfc extension.", "error");
      return;
    }

    try {
      selectedDump = parseDump(await file.text());
      document.getElementById("fileName").textContent = file.name;
      document.getElementById("ruid").textContent = selectedDump.ruid;
      document.getElementById("jsonPath").textContent = contentPath(
        selectedDump.folder,
        selectedDump.file,
        "json"
      );
      details.hidden = false;
      createButton.disabled = false;
      setStatus("The NFC dump is valid and ready to import.", "success");
    } catch (error) {
      setStatus(error.message, "error");
    }
  });

  createButton.addEventListener("click", async () => {
    if (!selectedDump) return;

    createButton.disabled = true;
    setStatus("Creating content JSON in TeddyCloud ...", "");

    try {
      const path = contentPath(
        selectedDump.folder,
        selectedDump.file,
        "json"
      );
      const exists = await contentFileExists(
        selectedDump.folder,
        `${selectedDump.file}.json`
      );
      if (exists && !window.confirm(`The file ${path} already exists. Overwrite it?`)) {
        setStatus("Import cancelled; the existing JSON file was left unchanged.", "");
        return;
      }

      const uploadedPath = await uploadJson(selectedDump);
      setStatus(
        `Content JSON created: ${uploadedPath}. TeddyCloud is now retrieving the TAF file from Boxine ...`,
        ""
      );
      await startTafCaching(selectedDump);
      await waitForCachedTaf(selectedDump);
    } catch (error) {
      setStatus(error.message, "error");
    } finally {
      createButton.disabled = false;
    }
  });
})();
