# -*- coding: utf-8 -*-

"""
This file is part of the Anki IPA add-on for Anki.
Add IPA transcription in Anki browser.
Copyright: (c) 2019 m-rtin <https://github.com/m-rtin>
License: GNU AGPLv3 <https://www.gnu.org/licenses/agpl.html>
"""
import urllib

from aqt.browser import Browser
from aqt.utils import tooltip, askUser
import aqt.qt as qt
import anki

from aqt import mw
CONFIG = mw.addonManager.getConfig(__name__)

from typing import List, Dict
from . import consts, parse_ipa_transcription, utils

class AddIpaTranscriptDialog(qt.QDialog):
    """QDialog to add IPA transcription to multiple notes in Anki browser."""

    def __init__(self, browser: Browser, selected_notes: List[int]) -> None:
        """ Initialize AddIpaTranscriptDialog and setup UI.

        :param browser: Anki browser
        :param selected_notes: IDs of selected notes in Anki browser
        """
        qt.QDialog.__init__(self, parent=browser)
        self.thread = qt.QThread()
        self.browser = browser
        self.selected_notes = selected_notes
        self._setup_comboboxes()
        self._setup_form()
        self._setup_buttons()
        self._setup_progressbar()
        self._setup_main()

    def _setup_comboboxes(self) -> None:
        """Setup comboboxes for language, base field and target field."""
        fields = self._get_fields()

        self.lang_combobox = qt.QComboBox()
        self.lang_combobox.addItems(consts.LANGUAGES_MAP.values())
        if "LANGUAGE" in CONFIG.keys():
            idx_language=self.lang_combobox.findText(CONFIG["LANGUAGE"])
            if idx_language > 0:
                self.lang_combobox.setCurrentIndex(idx_language)
        self.base_combobox = qt.QComboBox()
        self.base_combobox.addItems(fields)
        if "WORD_FIELD" in CONFIG.keys():
            idx_word=self.base_combobox.findText(CONFIG["WORD_FIELD"])
            if idx_word > 0:
                self.base_combobox.setCurrentIndex(idx_word)
        self.field_combobox = qt.QComboBox()
        self.field_combobox.addItems(fields)
        if "IPA_FIELD" in CONFIG.keys():
            idx_ipa=self.field_combobox.findText(CONFIG["IPA_FIELD"])
            if idx_ipa > 0:
                self.field_combobox.setCurrentIndex(idx_ipa)

    def _setup_form(self) -> None:
        """Setup form for user interaction."""
        self.form_group_box = qt.QGroupBox("Options")

        form_layout = qt.QFormLayout()
        form_layout.addRow(qt.QLabel("Language:"), self.lang_combobox)
        form_layout.addRow(qt.QLabel("Field of word:"), self.base_combobox)
        form_layout.addRow(qt.QLabel("Field of IPA transcription:"), self.field_combobox)

        self.form_group_box.setLayout(form_layout)

    def _setup_progressbar(self) -> None:
        """Setup progressbar which indicates how many IPA transcriptions already have been added."""
        self.progress = qt.QProgressBar(self)
        self.progress.setMinimum(0)
        self.progress.setMaximum(len(self.selected_notes))

    def _setup_buttons(self) -> None:
        """Setup add button."""
        button_box = qt.QDialogButtonBox(qt.Qt.Horizontal, self)
        add_button = button_box.addButton("Add", qt.QDialogButtonBox.ActionRole)

        self.bottom_hbox = qt.QHBoxLayout()
        self.bottom_hbox.addWidget(button_box)

        add_button.clicked.connect(self.on_confirm)

    def _setup_main(self) -> None:
        """Setup diag window."""
        main_layout = qt.QVBoxLayout()
        main_layout.addWidget(self.form_group_box)
        main_layout.addWidget(self.progress)
        main_layout.addLayout(self.bottom_hbox)
        self.setLayout(main_layout)
        self.setWindowTitle("Add IPA transcriptions")

    def _get_fields(self) -> None:
        """Get all fields of selected notes."""
        selected_note = self.selected_notes[0]
        mw = self.browser.mw
        model = mw.col.get_note(selected_note).model()
        fields = mw.col.models.field_names(model)
        return fields

    @qt.pyqtSlot(int)
    def on_progress_changed(self, value) -> None:
        """ Update progress bar value.

        :param value: new progressbar value
        """
        self.progress.setValue(value)

    def on_confirm(self) -> None:
        """Get IPA transcriptions for the selected notes.

        We get the IPA transcriptions by calling Worker in a different thread.
        Once it finishes we write the results into the right target fields of all the selected notes.
        We can't do this within the thread because SQLite doesn't support multi-threading.
        """
        question = f"This will overwrite the current content of the IPA transcription field. Proceed?"
        if not askUser(question, parent=self):
            return

        notes = self._create_note_dictionary()

        self.worker = Worker(notes, self.lang_combobox.currentText(), self.base_combobox.currentText())

        # connect methods
        self.worker.progress_changed.connect(self.on_progress_changed)
        self.worker.result.connect(self.add_ipa_transcription)
        self.worker.finished.connect(self.thread.quit)

        # thread management
        self.worker.moveToThread(qt.QThread.currentThread())
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.thread.finished.connect(self.close)
        self.thread.start()

    def _create_note_dictionary(self) -> Dict[int, anki.notes.Note]:
        """Map each note id to the corresponding Anki note object."""
        notes = {
            note: self.browser.mw.col.get_note(note)
            for note in self.selected_notes
        }
        return notes

    @qt.pyqtSlot(dict)
    def add_ipa_transcription(self, result_dict: Dict[int, str]) -> None:
        """ Add IPA transcriptions to the target fields of all selected notes.

        :param result_dict: dictionary of Anki notes and their IPA transcriptions
        """
        mw = self.browser.mw
        mw.checkpoint("add ipa transcription")
        mw.progress.start()
        self.browser.model.beginReset()

        for note_id, ipa_transcription in result_dict.items():
            note = mw.col.get_note(note_id)
            target_field = self.field_combobox.currentText()
            note[target_field] = ipa_transcription
            note.flush()

        self.browser.model.endReset()
        # mw.requireReset()
        mw.CollectionOp()
        mw.progress.finish()
        mw.reset()

    def closeEvent(self, event: qt.QCloseEvent) -> None:
        """ Stop worker and thread when window is closed by user.

        :param event: user wants to close window
        """
        if hasattr(self, 'worker'):
            self.worker.stop()
        self.thread.quit()
        event.accept()
        self.thread.wait()


class Worker(qt.QObject):
    """Worker to get the IPA transcriptions of the selected Anki notes."""

    finished = qt.pyqtSignal()
    progress_changed = qt.pyqtSignal(int)
    result = qt.pyqtSignal(dict)

    def __init__(self, notes: Dict[int, anki.notes.Note], lang: str, base_field: str) -> None:
        """ Initialize Worker.

        :param notes: Anki notes we want to use
        :param lang: language of base field content
        :param base_field: field for which we want to get IPA transcriptions
        """
        super().__init__()
        self.notes = notes
        self.lang = lang
        self.base_field = base_field
        self._isRunning = True

    @qt.pyqtSlot()
    def run(self) -> None:
        """Get IPA transcription for each note and save it into a dictionary."""
        new_dict = dict()
        for index, key in enumerate(self.notes.keys()):
            try:
                words = utils.get_words_from_field(field_text=self.notes[key][self.base_field])
                new_dict[key] = parse_ipa_transcription.transcript(words=words, language=self.lang)
            # IPA transcription not found
            except (urllib.error.HTTPError, IndexError):
                continue

            self.progress_changed.emit(index)

        self.result.emit(new_dict)
        self.finished.emit()

    def stop(self) -> None:
        """Stop worker."""
        self._isRunning = False


def on_batch_edit(browser: Browser) -> None:
    """ Open BatchEditDialog when menu entry is clicked.

    :param browser: Anki browser
    """
    selected_notes = browser.selectedNotes()
    if not selected_notes:
        tooltip("No cards selected.")
        return
    dialog = AddIpaTranscriptDialog(browser, selected_notes)
    dialog.exec_()


def setup_menu(browser: Browser) -> None:
    """ Add menu entry to Anki Browser.

    :param browser: Anki browser
    """
    menu = browser.form.menuEdit
    menu.addSeparator()
    a = menu.addAction('Add IPA ...')

    # call on_batch_edit if entry is clicked
    a.triggered.connect(lambda _, b=browser: on_batch_edit(b))
