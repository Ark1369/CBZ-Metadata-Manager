# 📚 CBZ Metadata Manager

**CBZ Metadata Manager** is a powerful desktop GUI tool for managing, editing, batch updating, and auto-filling `ComicInfo.xml` metadata in CBZ files using **Mangabaka** and **AniList**.

---

## 🚀 Key Features

* **Batch Metadata Editing**: Update hundreds of CBZ files at once.
* **Auto Metadata Fetching**: Uses local DB and online APIs (Mangabaka, AniList).
* **Fuzzy Matching & Aliases**: Smart filename/title matching with support for aliases.
* **Parallel Processing**: Leverage multiple CPU cores for fast batch updates.
* **Series Database GUI**: Manage reusable templates and aliases.
* **File/Folder Selection**: Drag-and-drop or dialog-based input for files/folders.
* **Bulk/Individual Modes**: Apply shared or file-specific metadata.
* **Auto Volume/Issue Extraction**: Parse volume/issue from filenames.
* **Page Counting**: Automatically counts images inside CBZ.
* **Integrated Tooltips**: Rich GUI tooltips for better usability.
* **Editable Interface**: Side-by-side comparison of original and updated metadata.
* **Extensible**: Easily add new sources or custom fields.

---
## **Screenshots**
<img width="1582" height="1019" alt="Screenshot 2025-08-04 154741" src="https://github.com/user-attachments/assets/0dcde122-0607-45ab-8496-c27481a244a8" />
<img width="891" height="628" alt="Screenshot 2025-08-04 154717" src="https://github.com/user-attachments/assets/605dad58-87f1-464b-8e13-e33f1ab312a9" />
<img width="795" height="625" alt="Screenshot 2025-08-04 154706" src="https://github.com/user-attachments/assets/6686a015-e043-4288-9406-a58576d31f9a" />
<img width="2445" height="1015" alt="Screenshot 2025-08-04 173021" src="https://github.com/user-attachments/assets/75fe474c-54da-41b1-b581-09edf67ba2ca" />

---

## 📑 Table of Contents

* [Installation](#installation)
* [Usage](#usage)
* [Features & Workflow](#features--workflow)
* [Metadata Sources](#metadata-sources)
* [Database Architecture](#database-architecture)
* [Project Structure](#project-structure)
* [Development & Customization](#development--customization)
* [Troubleshooting & FAQ](#troubleshooting--faq)
* [License](#license)
* [Contributing](#contributing)
* [Appendix: Developer Notes](#appendix-developer-notes)

---

## 🛠 Installation

### Requirements

* Python 3.8+

* Install dependencies: pip install requests


### Run

```bash
python cbz_metadata_manager.py
```
or
```bash
uv cbz_metadata_manager.py
```
---

## 📖 Usage

### 0. Download and Place the **Mangabaka JSONL Database** [https://mangabaka.dev/database](https://mangabaka.dev/database) in the folder script is in. For sustained/heavy usage, this is way to go as it makes everything goes much faster.

### 1. Load CBZ Files

* Click **Select CBZ** to choose multiple files.
* Or use **Select Folder** for batch loading a directory.
* Loaded files appear in a navigation panel.

### 2. Choose Metadata Mode

* **Same Metadata per File Mode**: Same metadata for all files. (Choice when doing a single series)
* **Dfifferent Metadata per File Mode**: Extracted or fetched metadata per file. (Choice when doing an updates dump)

### 3. Fetch Metadata

* **Fetch Metadata**: Uses Local dump, APIs, and your DB.
  * **Local Only mode**: Only uses Local JSONL DB to eliminate API calls and slowness.
* **Fetch AniList**: Adds characters and staff info from Anilist, requires Mangabaka Fetch to be done prior.

### 4. Edit/Review Metadata

* **Side-by-Side Edit**: Compare original vs new values, use dropdowns.
* **Bulk Edit Toggle**: Apply changes to all files.

### 5. Use the Series Database

* **Save Series**: Create reusable metadata templates with or without Alias.
* **Edit Aliases**: Add alternate titles.
* **Manage Series**: View, edit, or match series.
* **Match Series**: Instantly load a saved matching series Metadata.

### 6. Insert Metadata

* **Insert to CBZs**: Writes ComicInfo.xml in parallel threads.
* **Progress Dialog**: Visual feedback with cancel button.

---

## 🔁 Features & Workflow

### Automation-First Design

* **Smart Filename Parsing**: Strips tags and identifies titles/volumes.
* **Merge-Aware Matching**: Supports outdated/merged titles.
* **Fuzzy Matching**: Tolerates typos or alternate spellings.
* **Alias System**: Multiple aliases per series.
* **URL Auto-fill**: Auto-populates web links (AniList, publishers, etc.).

### Local Storage with SQLite

* Persistent storage of templates.
* One-to-many alias linking with cascading delete.
* Indexes for ultra-fast lookups.

---

## 🌐 Metadata Sources

* **Local JSONL Dump**: Offline batch metadata (from Mangabaka).
* **Mangabaka API**: Genre, tags, publisher, etc.
* **AniList API**: Characters, staff, series metadata.
* **Filename Parsing**: Fallback when data is missing.

---

## 🗃️ Database Architecture

### Tables

* `series_metadata`: Stores (series\_name, metadata\_json, updated\_at).
* `series_aliases`: Stores (series\_name, alias).
  Foreign key with `ON DELETE CASCADE`.

### JSON Schema

* Each series metadata stored as JSON blob for flexibility.

---

## 🗂 Project Structure

```text
cbz_metadata_manager.py      # Main GUI + logic
├── GUI classes              # Main window, series/alias dialogs
├── Metadata fetchers        # Local + online
├── DB wrapper               # CRUD, aliasing, search
├── Parallel inserter        # Multithreaded metadata writer
├── Logging                  # Debug and process tracing
├── ...
series.jsonl                 # Optional local dump
api_cache.json               # Optional API call cache
metadata_database.db         # SQLite DB
cbz_metadata.log             # Debug log file
```

---

## 🛠 Development & Customization

### Structure

* **Modular**: Clear GUI/API/DB separation.
* **Class-based**: Extend dialogs, matchers, editors easily.
* **Multithreaded**: Fast metadata injection with cancel option.

### Add Fields

* Add to `self.fields` — GUI and logic adjust automatically.

### Add Sources

* Modify `get_metadata_from_dump_or_api()` or
  `extract_metadata()` to support more APIs.

---

## 🧩 Troubleshooting & FAQ

* **File errors**: Check `cbz_metadata.log` for stack traces.
* **API limits**: Retry/backoff built-in; edit rate in code if needed.
* **Matching issues**: Improve regex or alias definitions.
* **Slow processing**: Tune thread count for your system.

---

## 📄 License

**MIT License** — See `LICENSE` file.
Special thanks to **Mangabaka** and **AniList** for providing API and metadata dumps.

---

## 🤝 Contributing

Pull requests welcome for:

* Metadata source integrations
* UX enhancements
* Feature suggestions

Please file issues with debug logs and reproduction steps.

---

## 🧪 Appendix: Developer Notes

* **Logging**: All core actions/errors logged to `cbz_metadata.log`.
* **Thread Safety**: Locking ensures safe parallel metadata injection.
* **GUI Responsiveness**: Uses `Tk.after()` for thread-safe updates.
* **Performance-Oriented**: Processes thousands of CBZs with low memory use via streaming and chunking.

---

💡 **Tip**: Build your local series DB as you go! Once filled, even huge collections can be auto-tagged offline with smart reuse.
💡 **Tip**: When in doubt, please read the Tooltips.


## 🧑‍💻 Author
* Built using Python and AI-assisted development.
