"""Exercise patched preset callbacks with a deferred fake picker (not a native UI test).

Run: python3 tests/preset_file_dialogs.py
Requires the pinned external/vital submodule and g++ with ASan/UBSan.
"""
from pathlib import Path
import subprocess
import tempfile

stubs = r'''
#include <cassert>
#include <functional>
#include <iostream>
#include <memory>
#include <stdexcept>
#include <string>
using String = std::string;
namespace vital { const char* kPresetExtension = "vital"; }
struct File {
  std::string path;
  File(std::string p = "") : path(p) {}
  bool exists() const { return !path.empty() && path != "missing"; }
  bool existsAsFile() const { return exists() && path != "directory"; }
  File withFileExtension(const char* ext) const { return File(path + "." + ext); }
  bool operator==(const File& f) const { return path == f.path; }
};
struct FileBrowserComponent {
  enum FileChooserFlags { openMode=1, saveMode=2, canSelectFiles=4, warnAboutOverwriting=8 };
};
struct FileChooser {
  static FileChooser* active;
  File result;
  int flags = 0;
  std::function<void(const FileChooser&)> callback;
  FileChooser(const char*, File, String) {}
  ~FileChooser() { if (active == this) active = nullptr; }
  File getResult() const { return result; }
  void launchAsync(int f, std::function<void(const FileChooser&)> cb) { flags=f; callback=cb; active=this; }
  static void complete(File file) { active->result=file; auto cb=active->callback; cb(*active); }
};
FileChooser* FileChooser::active = nullptr;
struct Component {
  std::shared_ptr<int> life = std::make_shared<int>(0);
  template<class T> struct SafePointer {
    T* pointer;
    std::weak_ptr<int> life;
    SafePointer(T* p) : pointer(p), life(p->life) {}
    bool operator==(std::nullptr_t) const { return life.expired(); }
    T* operator->() const { return life.expired() ? nullptr : pointer; }
  };
};
struct SynthBase {
  File active = File("previous.vital");
  int loads=0, saves=0;
  bool load_ok=true, save_ok=true;
  File getActiveFile() { return active; }
  bool loadFromFile(File f, std::string& error) {
    ++loads; error="Invalid preset"; if (load_ok) active=f; return load_ok;
  }
  bool saveToFile(File f) { ++saves; if (save_ok) active=f; return save_ok; }
};
struct SynthGuiInterface {
  SynthBase synth;
  int notifications=0;
  SynthBase* getSynth() { return &synth; }
  void externalPresetLoaded(File) { ++notifications; }
};
struct AlertWindow {
  enum class AlertIconType { WarningIcon };
  static int warnings;
  static void showMessageBoxAsync(AlertIconType, String, String) { ++warnings; }
};
int AlertWindow::warnings=0;
struct SynthPresetSelector : Component {
  SynthGuiInterface* parent;
  CHOOSER_MEMBER
  SynthPresetSelector(SynthGuiInterface* p) : parent(p) {}
  template<class T> T* findParentComponentOfClass() { return static_cast<T*>(parent); }
  void importPreset();
  void exportPreset();
};
void require(bool ok, const char* message) { if (!ok) throw std::runtime_error(message); }
'''
tests = r'''
int main() {
  try {
    SynthGuiInterface gui;
    SynthPresetSelector selector(&gui);
    selector.importPreset();
    require(FileChooser::active, "Import chooser was destroyed before user selection");
    require(FileChooser::active->flags == (FileBrowserComponent::openMode | FileBrowserComponent::canSelectFiles), "Import must select files");
    FileChooser::complete(File("loaded.vital"));
    require(gui.synth.loads==1 && gui.notifications==1, "Import success not reported");
    selector.importPreset(); FileChooser::complete(File());
    require(gui.synth.loads==1 && gui.notifications==1 && AlertWindow::warnings==0, "Import cancel changed state");
    selector.importPreset(); FileChooser::complete(File("missing"));
    selector.importPreset(); FileChooser::complete(File("directory"));
    require(gui.synth.loads==1, "Non-file selection loaded");
    gui.synth.load_ok=false;
    selector.importPreset(); FileChooser::complete(File("broken.vital"));
    require(gui.synth.loads==2 && gui.notifications==1 && AlertWindow::warnings==1, "Import failure must show error without success");
    selector.exportPreset();
    require(FileChooser::active, "Export chooser was destroyed before user selection");
    require(FileChooser::active->flags == (FileBrowserComponent::saveMode | FileBrowserComponent::canSelectFiles | FileBrowserComponent::warnAboutOverwriting), "Export must select files and warn before overwrite");
    FileChooser::complete(File());
    require(gui.synth.saves==0 && gui.notifications==1 && AlertWindow::warnings==1, "Export cancel changed state");
    selector.exportPreset(); FileChooser::complete(File("export"));
    require(gui.synth.saves==1 && gui.synth.active.path=="export.vital" && gui.notifications==2, "Export success not reported");
    gui.synth.save_ok=false;
    selector.exportPreset(); FileChooser::complete(File("readonly"));
    require(gui.synth.saves==2 && gui.notifications==2 && AlertWindow::warnings==2, "Export failure must show error without success");
    selector.importPreset(); selector.parent=nullptr; FileChooser::complete(File("gone.vital"));
    require(gui.synth.loads==2, "Import used detached parent");
    selector.parent=&gui; selector.exportPreset(); selector.parent=nullptr; FileChooser::complete(File("gone.vital"));
    require(gui.synth.saves==2, "Export used detached parent");
    SynthPresetSelector detached(nullptr); detached.importPreset(); detached.exportPreset();
    {
      SynthPresetSelector closing(&gui); closing.importPreset();
      require(FileChooser::active, "Missing chooser before teardown");
    }
    require(FileChooser::active==nullptr, "Chooser survived selector teardown");
    std::cout << "PASS: deferred import/export, flags, cancellation, invalid selection, errors, detached parent, teardown\n";
  } catch (const std::exception& e) { std::cerr << "FAIL: " << e.what() << '\n'; return 1; }
}
'''
with tempfile.TemporaryDirectory() as tmp:
    root = Path(__file__).resolve().parents[1]
    relative = Path('src/interface/editor_components/synth_preset_selector')
    for extension in ('.cpp', '.h'):
        path = relative.with_suffix(extension)
        original = subprocess.check_output(
            ['git', '-C', str(root/'external/vital'), 'show', 'HEAD:' + str(path)])
        target = Path(tmp)/path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(original)
    subprocess.run(['git', 'apply', '--include=' + str(relative) + '.*',
                    str(root/'apps/vitaloid-aap.patch')], cwd=tmp, check=True)
    source = (Path(tmp)/relative.with_suffix('.cpp')).read_text()
    header = (Path(tmp)/relative.with_suffix('.h')).read_text()
    member = next(line.strip() for line in header.splitlines()
                  if 'std::unique_ptr<FileChooser>' in line)
    methods = source[source.index('void SynthPresetSelector::importPreset()'):
                     source.index('void SynthPresetSelector::importBank()')]
    cpp=Path(tmp)/'test.cpp'
    cpp.write_text(stubs.replace('CHOOSER_MEMBER', member)+methods+tests)
    binary=Path(tmp)/'test'
    subprocess.run(['g++','-std=c++14','-g','-fsanitize=address,undefined','-fno-omit-frame-pointer',str(cpp),'-o',str(binary)],check=True)
    subprocess.run([str(binary)],check=True)
