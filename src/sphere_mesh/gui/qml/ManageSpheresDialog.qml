import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Dialog {
    id: dialog
    objectName: "manageSpheresDialog"
    required property var backend
    property color foreground: "#17243b"
    property color muted: "#65748c"
    property color cardColor: "#ffffff"
    property color borderColor: "#e1e8f1"
    property color danger: "#b83c45"
    readonly property int discoveredCount: (backend.enrollmentCandidates || []).length

    title: qsTr("Manage Spheres")
    modal: true
    anchors.centerIn: parent
    width: Math.min(parent.width - 24, 420)
    height: Math.min(parent.height - 40, 650)
    standardButtons: Dialog.Close
    onOpened: backend.refreshDevices()
    onClosed: backend.stopEnrollment()
    onDiscoveredCountChanged: {
        if (discoveredCount > 0 && (backend.enrollment || ({})).active) {
            Qt.callLater(function() {
                discoveredList.positionViewAtEnd()
                var view = managementScroll.contentItem
                if (view && view.contentY !== undefined) {
                    var point = discoveredList.mapToItem(view.contentItem, 0, 0)
                    view.contentY = Math.max(0, Math.min(point.y,
                                            view.contentHeight - view.height))
                }
            })
        }
    }

    function hasState(state) {
        var candidates = (backend.enrollment || ({})).candidates || []
        for (var i = 0; i < candidates.length; ++i) {
            if (candidates[i].state === state)
                return true
        }
        return false
    }

    function nameAvailable(name, exceptDevice) {
        var normalized = name.trim().toLocaleLowerCase()
        if (!normalized || name.trim().length > 64 || /^0x[0-9a-f]+$/i.test(name.trim()))
            return false
        var devices = backend.devices || []
        for (var i = 0; i < devices.length; ++i) {
            if (devices[i].device !== exceptDevice
                    && String(devices[i].name || "").trim().toLocaleLowerCase() === normalized)
                return false
        }
        return true
    }

    ScrollView {
        id: managementScroll
        anchors.fill: parent
        clip: true
        ScrollBar.horizontal.policy: ScrollBar.AlwaysOff

        ColumnLayout {
            width: managementScroll.availableWidth
            spacing: 12
            Label {
                Layout.fillWidth: true
                text: qsTr("Your Spheres")
                font.pixelSize: 16
                font.weight: Font.DemiBold
                color: dialog.foreground
            }
            Label {
                Layout.fillWidth: true
                visible: (dialog.backend.devices || []).length === 0
                text: qsTr("No Spheres enrolled yet.")
                color: dialog.muted
                wrapMode: Text.WordWrap
            }
            ListView {
                id: spheresList
                objectName: "spheresList"
                Layout.fillWidth: true
                Layout.preferredHeight: Math.min(contentHeight,
                                                (dialog.backend.enrollment || ({})).active ? 124 : 210)
                Layout.maximumHeight: Layout.preferredHeight
                Layout.fillHeight: false
                visible: count > 0
                clip: true
                spacing: 0
                model: dialog.backend.devices || []
                ScrollBar.vertical: ScrollBar { policy: spheresList.contentHeight > spheresList.height ? ScrollBar.AlwaysOn : ScrollBar.AsNeeded }
                delegate: Rectangle {
                    objectName: "enrolledSphereRow"
                    required property var modelData
                    width: spheresList.width
                    height: Math.ceil(rowFontMetrics.height) + 6
                    radius: 4
                    color: dialog.cardColor
                    border.color: dialog.borderColor
                    FontMetrics {
                        id: rowFontMetrics
                        font: sphereName.font
                    }
                    RowLayout {
                        anchors.fill: parent
                        anchors.leftMargin: 6
                        anchors.rightMargin: 6
                        Label {
                            id: sphereName
                            objectName: "enrolledSphereName"
                            Layout.fillWidth: true
                            Layout.fillHeight: true
                            verticalAlignment: Text.AlignVCenter
                            text: modelData.name || qsTr("Unnamed Sphere")
                            color: dialog.foreground
                            elide: Text.ElideRight
                        }
                        Button {
                            Layout.minimumHeight: 0
                            Layout.preferredHeight: parent.height
                            Layout.maximumHeight: parent.height
                            topPadding: 0
                            bottomPadding: 0
                            topInset: 0
                            bottomInset: 0
                            text: qsTr("Rename")
                            flat: true
                            enabled: dialog.backend.serviceAvailable && !dialog.backend.managementPending
                                     && !(dialog.backend.enrollment || ({})).active
                            onClicked: {
                                renameDialog.device = modelData.device
                                renameField.text = modelData.name || ""
                                renameDialog.open()
                            }
                        }
                        Button {
                            Layout.minimumHeight: 0
                            Layout.preferredHeight: parent.height
                            Layout.maximumHeight: parent.height
                            topPadding: 0
                            bottomPadding: 0
                            topInset: 0
                            bottomInset: 0
                            text: qsTr("Forget")
                            flat: true
                            enabled: dialog.backend.serviceAvailable && !dialog.backend.managementPending
                                     && !(dialog.backend.enrollment || ({})).active
                            onClicked: {
                                forgetDialog.device = modelData.device
                                forgetDialog.deviceName = modelData.name || qsTr("this Sphere")
                                forgetDialog.open()
                            }
                        }
                    }
                }
            }
            Label {
                Layout.fillWidth: true
                Layout.topMargin: 8
                text: qsTr("Add a Sphere")
                font.pixelSize: 16
                font.weight: Font.DemiBold
                color: dialog.foreground
            }
            Label {
                Layout.fillWidth: true
                text: qsTr("Put each Sphere into pairing mode with three quick presses, then start enrollment. Compatible 0.5 Spheres are enrolled automatically with unique default names. You can rename them afterward.")
                color: dialog.muted
                wrapMode: Text.WordWrap
            }
            Button {
                objectName: "enrollmentToggle"
                text: (dialog.backend.enrollment || ({})).active ? qsTr("Stop enrollment") : qsTr("Enroll nearby Spheres")
                enabled: dialog.backend.serviceAvailable && !dialog.backend.managementPending
                onClicked: {
                    if ((dialog.backend.enrollment || ({})).active)
                        dialog.backend.stopEnrollment()
                    else
                        dialog.backend.startEnrollment()
                }
            }
            Label {
                objectName: "enrollmentProgress"
                Layout.fillWidth: true
                visible: !!(dialog.backend.enrollment || ({})).active
                text: (dialog.hasState("recovering") ? qsTr("Finishing setup…")
                       : (dialog.backend.enrollment || ({})).phase === "checking" ? qsTr("Checking compatibility…")
                       : (dialog.backend.enrollment || ({})).phase === "provisioning"
                         || (dialog.backend.enrollment || ({})).phase === "configuring"
                         || dialog.hasState("provisioning") ? qsTr("Provisioning…")
                       : qsTr("Discovering…"))
                      + " " + qsTr("%1 seconds remaining. Stopping enrollment finishes provisioning already started.").arg((dialog.backend.enrollment || ({})).remaining_seconds || 0)
                color: dialog.muted
                wrapMode: Text.WordWrap
            }
            ListView {
                id: discoveredList
                objectName: "discoveredSpheresList"
                Layout.fillWidth: true
                Layout.preferredHeight: Math.min(contentHeight, 180)
                Layout.maximumHeight: Layout.preferredHeight
                Layout.fillHeight: false
                visible: count > 0
                clip: true
                spacing: 4
                model: dialog.backend.enrollmentCandidates || []
                ScrollBar.vertical: ScrollBar { policy: discoveredList.contentHeight > discoveredList.height ? ScrollBar.AlwaysOn : ScrollBar.AsNeeded }
                delegate: Item {
                    required property var modelData
                    width: discoveredList.width
                    height: candidateColumn.implicitHeight + 4
                    ColumnLayout {
                        id: candidateColumn
                        width: parent.width
                        spacing: 1
                        Label {
                            objectName: "candidateName"
                            Layout.fillWidth: true
                            text: modelData.name || qsTr("Sphere")
                            color: dialog.foreground
                            wrapMode: Text.WordWrap
                        }
                        Label {
                            objectName: "candidateStatus"
                            Layout.fillWidth: true
                            text: modelData.state === "failed" || modelData.state === "error"
                                  ? ((dialog.backend.enrollment || ({})).active
                                     ? qsTr("Could not finish after several attempts. Enrollment continues for other Spheres; stop and start enrollment to retry this one.")
                                     : qsTr("Could not finish. Start discovery to retry."))
                                  : modelData.state === "retrying" ? qsTr("Connection interrupted. Retrying automatically…")
                                  : modelData.state === "recovering"
                                    ? ((dialog.backend.enrollment || ({})).active
                                       ? qsTr("Network details saved. Finishing setup automatically; leave this Sphere on.")
                                       : qsTr("Network details saved. Start discovery to resume setup; leave this Sphere on."))
                                  : modelData.state === "complete" || modelData.state === "enrolled" ? qsTr("Enrolled")
                                  : modelData.state === "unsupported" ? qsTr("Not compatible")
                                  : modelData.state === "checking" ? qsTr("Checking compatibility…")
                                  : modelData.state === "provisioning" ? qsTr("Provisioning…")
                                  : modelData.state === "found" ? qsTr("Found")
                                  : qsTr("Enrollment in progress…")
                            color: modelData.state === "failed" || modelData.state === "error" ? dialog.danger : dialog.muted
                            wrapMode: Text.WordWrap
                        }
                    }
                }
            }
            Label {
                Layout.fillWidth: true
                visible: !!dialog.backend.managementError || (((dialog.backend.enrollment || ({})).errors || []).length > 0
                         && !dialog.hasState("recovering") && !dialog.hasState("complete")
                         && !dialog.hasState("retrying") && !dialog.hasState("failed"))
                text: dialog.backend.managementError || qsTr("Enrollment failed. Please try again.")
                color: dialog.danger
                wrapMode: Text.WordWrap
            }
            Label {
                Layout.fillWidth: true
                visible: !dialog.backend.serviceAvailable
                text: qsTr("The Sphere service is unavailable.")
                color: dialog.danger
                wrapMode: Text.WordWrap
            }
        }
    }

    Dialog {
        id: renameDialog
        objectName: "renameDialog"
        parent: Overlay.overlay
        property string device: ""
        property bool submitted: false
        title: qsTr("Rename Sphere")
        modal: true
        anchors.centerIn: parent
        width: Math.min(parent.width - 32, 360)
        standardButtons: Dialog.Cancel
        onOpened: { submitted = false; renameField.forceActiveFocus() }
        function save() {
            if (!saveButton.enabled || submitted)
                return
            submitted = true
            dialog.backend.renameDevice(device, renameField.text.trim())
            close()
        }
        ColumnLayout {
            anchors.fill: parent
            TextField {
                id: renameField
                objectName: "renameNameField"
                Layout.fillWidth: true
                placeholderText: qsTr("Sphere name")
                maximumLength: 64
                Accessible.name: qsTr("Sphere name")
                onAccepted: renameDialog.save()
            }
            Label {
                Layout.fillWidth: true
                visible: renameField.text.trim().length > 0 && !dialog.nameAvailable(renameField.text, renameDialog.device)
                text: qsTr("Choose a unique name.")
                color: dialog.danger
                wrapMode: Text.WordWrap
            }
            Button {
                id: saveButton
                objectName: "renameSaveButton"
                text: qsTr("Save")
                enabled: dialog.backend.serviceAvailable && !dialog.backend.managementPending
                         && !(dialog.backend.enrollment || ({})).active
                         && dialog.nameAvailable(renameField.text, renameDialog.device)
                onClicked: renameDialog.save()
            }
        }
    }
    Dialog {
        id: forgetDialog
        objectName: "forgetDialog"
        parent: Overlay.overlay
        property string device: ""
        property string deviceName: ""
        title: qsTr("Forget Sphere?")
        modal: true
        anchors.centerIn: parent
        width: Math.min(parent.width - 32, 360)
        standardButtons: Dialog.Cancel
        ColumnLayout {
            anchors.fill: parent
            Label {
                Layout.fillWidth: true
                text: qsTr("Forget %1 locally? This does not reset the physical Sphere. To pair it again, put it in pairing mode with three quick presses.").arg(forgetDialog.deviceName)
                color: dialog.foreground
                wrapMode: Text.WordWrap
            }
            Button {
                text: qsTr("Forget")
                enabled: dialog.backend.serviceAvailable && !dialog.backend.managementPending
                         && !(dialog.backend.enrollment || ({})).active
                onClicked: {
                    dialog.backend.forgetDevice(forgetDialog.device)
                    forgetDialog.close()
                }
            }
        }
    }
}
