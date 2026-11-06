import QtQuick
import QtQuick.Controls
import QtQuick.Controls.Material
import QtQuick.Layouts

ApplicationWindow {
    id: root

    width: 440
    height: 760
    minimumWidth: 320
    minimumHeight: 520
    visible: true
    title: qsTr("Spherosphere")

    property int currentPage: 0
    property string effectCategory: "pattern"
    property string displayedDevice: ""
    property bool synchronizeSource: false
    readonly property bool previewBrightnessKnown: (sphere.effectActive || sphere.colorModeActive)
        ? (sphere.hsl || ({})).lightness !== undefined
        : sphere.lightness !== null && sphere.lightness !== undefined
    readonly property real currentPreviewBrightness: previewBrightnessKnown ? brightnessSlider.previewValue : 50
    readonly property bool darkMode: systemPalette.window.hslLightness < 0.5
    readonly property color canvasColor: darkMode ? "#111a2a" : "#f5f7fb"
    readonly property color cardColor: darkMode ? "#1b2940" : "#ffffff"
    readonly property color foreground: darkMode ? "#f2f5fb" : "#17243b"
    readonly property color muted: darkMode ? "#a5b2c7" : "#65748c"
    readonly property color borderColor: darkMode ? "#34445d" : "#e1e8f1"
    readonly property color accent: "#6e7ce8"
    readonly property color success: darkMode ? "#74d8ad" : "#198663"
    readonly property color danger: darkMode ? "#ffaaa1" : "#b83c45"
    readonly property bool controlsReady: sphere.connected && !sphere.pending
    readonly property bool batteryKnown: sphere.battery !== null
                                         && sphere.battery !== undefined
                                         && Number(sphere.battery) >= 0
    readonly property color previewColor: sphere.power === true && capability("hsl")
                                          ? Qt.hsva(hueSlider.previewValue / 360,
                                                    saturationSlider.previewValue / 100,
                                                    root.previewBrightness(currentPreviewBrightness), 1)
                                          : accent

    color: canvasColor
    Material.accent: accent
    Material.primary: accent
    Material.theme: darkMode ? Material.Dark : Material.Light

    function capability(name) {
        var available = sphere.capabilities || ({})
        return available[name] === true
    }

    function hsvValue(name) {
        var values = sphere.hsv || ({})
        return Number(values[name] || 0)
    }

    function currentEffectMedia() {
        var slug = (sphere.effect || ({})).slug
        var catalog = sphere.effects || []
        for (var index = 0; index < catalog.length; index++) {
            if (catalog[index].slug === slug)
                return catalog[index]
        }
        return ({})
    }

    function effectLabel() {
        var selection = sphere.effect || ({})
        return selection.label || ""
    }

    function synchronizePreviewSource() {
        if (displayedDevice !== sphere.selectedDevice) {
            displayedDevice = sphere.selectedDevice
            synchronizeSource = !!displayedDevice
        }
        if (!synchronizeSource || !sphere.connected || sphere.previewKind === "unknown")
            return
        if (sphere.previewKind === "effect") {
            var media = currentEffectMedia()
            if (!media.slug)
                return
            effectCategory = media.category || "pattern"
            effectSearch.text = ""
            currentPage = 1
            Qt.callLater(function() {
                var view = effectsScroll.contentItem
                if (!view || view.contentY === undefined)
                    return
                for (var index = 0; index < effectRepeater.count; ++index) {
                    var tile = effectRepeater.itemAt(index)
                    if (tile && tile.effectSlug === (sphere.effect || ({})).slug) {
                        var point = tile.mapToItem(view.contentItem, 0, 0)
                        view.contentY = Math.max(0, Math.min(point.y - 12,
                                            view.contentHeight - view.height))
                        break
                    }
                }
            })
        } else {
            currentPage = 0
        }
        synchronizeSource = false
    }

    Component.onCompleted: root.synchronizePreviewSource()

    Connections {
        target: sphere
        function onStateChanged() { root.synchronizePreviewSource() }
        function onEffectsChanged() { root.synchronizePreviewSource() }
    }

    function brightnessPosition(lightness) {
        var level = Number(lightness)
        if (!isFinite(level) || level <= 1966)
            return 0
        return 100 * Math.log(Math.min(65535, level) / 1966) / Math.log(65535 / 1966)
    }

    function brightnessLightness(position) {
        var fraction = Math.max(0, Math.min(1, position / 100))
        return Math.round(1966 * Math.pow(65535 / 1966, fraction))
    }

    function previewBrightness(position) {
        return 0.62 + 0.38 * Math.max(0, Math.min(1, position / 100))
    }

    // The knob already expresses perceived brightness; do not exponentiate it again.
    // Dim source footage needs headroom above its unchanged level at 25%.
    function effectPreviewBrightness(position) {
        return 0.2 + 3.2 * Math.max(0, Math.min(1, position / 100))
    }

    function applySelectedColor() {
        sphere.activateColor(brightnessLightness(brightnessSlider.previewValue),
                             Math.round(hueSlider.previewValue * 65535 / 360),
                             Math.round(saturationSlider.previewValue * 65535 / 100))
    }

    function selectEffect(slug) {
        if (slug === "color-wheel") {
            currentPage = 0
            applySelectedColor()
        } else {
            sphere.activateEffect(slug)
        }
    }

    function matchingEffects(query) {
        var matches = []
        var catalog = sphere.effects || []
        var needle = query.toLowerCase()
        for (var index = 0; index < catalog.length; index++) {
            var effect = catalog[index]
            var label = effect.label || effect.name || ""
            if (effect.visible !== false && (effect.category || "pattern") === effectCategory
                    && label.toLowerCase().indexOf(needle) !== -1)
                matches.push(effect)
        }
        return matches
    }

    function readableDetails() {
        var details = sphere.errorDetails
        if (!details || Object.keys(details).length === 0)
            return ""
        try {
            return JSON.stringify(details, null, 2)
        } catch (error) {
            return String(details)
        }
    }

    function selectedDeviceIndex() {
        var devices = sphere.devices || []
        for (var i = 0; i < devices.length; ++i) {
            if (devices[i].device === sphere.selectedDevice)
                return i
        }
        return -1
    }

    onActiveChanged: {
        if (active)
            sphere.refresh()
    }

    SystemPalette { id: systemPalette }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: root.width < 380 ? 14 : 20
        spacing: 0

        RowLayout {
            Layout.fillWidth: true
            Layout.preferredHeight: 62
            spacing: 12

            Rectangle {
                Layout.preferredWidth: 35
                Layout.preferredHeight: 35
                radius: 11
                color: root.darkMode ? "#29365a" : "#e7eaff"

                Rectangle {
                    anchors.centerIn: parent
                    width: 22
                    height: 22
                    radius: 11
                    gradient: Gradient {
                        GradientStop { position: 0; color: "#eee5ff" }
                        GradientStop { position: 0.4; color: "#ad9afa" }
                        GradientStop { position: 1; color: "#4d53bd" }
                    }

                    Rectangle {
                        x: 5
                        y: 4
                        width: 6
                        height: 4
                        radius: 3
                        color: "#ffffff"
                        opacity: 0.75
                    }
                }
            }

            ColumnLayout {
                Layout.fillWidth: true
                spacing: 1

                Label {
                    text: qsTr("Spherosphere")
                    color: root.foreground
                    font.pixelSize: 22
                    font.weight: Font.Bold
                }

            }

            Rectangle {
                implicitWidth: connectionText.implicitWidth + 24
                implicitHeight: 30
                radius: 15
                color: sphere.connected
                       ? (root.darkMode ? "#1a453e" : "#e2f5ed")
                       : (root.darkMode ? "#3a3444" : "#f0eaf0")
                border.color: sphere.connected ? root.success : root.borderColor
                border.width: 1

                Label {
                    id: connectionText
                    objectName: "connectionStatusLabel"
                    anchors.centerIn: parent
                    text: !sphere.serviceAvailable
                          ? (sphere.serviceState === "connecting" ? qsTr("Connecting")
                             : sphere.serviceState === "unresponsive" ? qsTr("Daemon busy")
                             : sphere.serviceState === "invalid_response" ? qsTr("Daemon error")
                             : qsTr("Daemon offline"))
                          : (sphere.enrollment || ({})).active ? qsTr("Enrolling")
                          : !sphere.selectedDevice ? qsTr("No Sphere")
                          : sphere.connected ? qsTr("Connected") : qsTr("Mesh offline")
                    color: sphere.connected ? root.success : root.muted
                    font.pixelSize: 11
                    font.weight: Font.DemiBold
                }
            }
        }

        RowLayout {
            Layout.fillWidth: true
            Layout.bottomMargin: 12
            spacing: 8

            ComboBox {
                objectName: "deviceSelector"
                Layout.fillWidth: true
                model: sphere.devices || []
                textRole: "name"
                currentIndex: root.selectedDeviceIndex()
                enabled: sphere.serviceAvailable && !sphere.managementPending && !sphere.pending
                         && !(sphere.enrollment || ({})).active && count > 0
                displayText: currentIndex >= 0 ? currentText : qsTr("Select a Sphere")
                onActivated: function(index) {
                    sphere.selectDevice(model[index].device)
                }
                Accessible.name: qsTr("Selected Sphere")
            }

            Button {
                objectName: "manageSpheresButton"
                text: "+"
                font.pixelSize: 22
                enabled: sphere.serviceAvailable
                Accessible.name: qsTr("Manage Spheres")
                onClicked: manageDialog.open()
            }
        }

        Rectangle {
            objectName: "feedbackCard"
            visible: !!sphere.errorMessage || sphere.pending || !sphere.connected
            Layout.fillWidth: true
            Layout.preferredHeight: visible ? feedbackColumn.implicitHeight + 20 : 0
            Layout.bottomMargin: visible ? 12 : 0
            radius: 14
            color: sphere.errorMessage ? (root.darkMode ? "#402b38" : "#fff0ef")
                   : sphere.pending ? (root.darkMode ? "#263750" : "#ebefff")
                   : root.cardColor
            border.width: 1
            border.color: sphere.errorMessage ? root.danger : root.borderColor

            ColumnLayout {
                id: feedbackColumn
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.verticalCenter: parent.verticalCenter
                anchors.leftMargin: 14
                anchors.rightMargin: 14
                spacing: 4

                RowLayout {
                    Layout.fillWidth: true
                    spacing: 8

                    BusyIndicator {
                        Layout.preferredWidth: 20
                        Layout.preferredHeight: 20
                        running: sphere.pending
                        visible: running
                    }

                    Label {
                        objectName: "connectionProblemTitle"
                        Layout.fillWidth: true
                        text: !sphere.serviceAvailable
                              ? (sphere.serviceState === "connecting" ? qsTr("Connecting to the Sphere daemon…")
                                 : sphere.serviceState === "unresponsive" ? qsTr("The Sphere daemon is not responding")
                                 : sphere.serviceState === "invalid_response" ? qsTr("Unexpected response from the Sphere daemon")
                                 : qsTr("Cannot reach the Sphere daemon"))
                              : sphere.errorMessage ? sphere.errorMessage
                              : sphere.pending ? (sphere.pendingAction || qsTr("Updating Sphere…"))
                              : (sphere.enrollment || ({})).active ? qsTr("Enrollment in progress")
                              : !sphere.selectedDevice ? qsTr("Add or select a Sphere to begin")
                              : qsTr("The daemon is running, but Bluetooth Mesh is unavailable")
                        color: sphere.errorMessage || (!sphere.serviceAvailable && sphere.serviceState !== "connecting")
                               ? root.danger : root.foreground
                        font.pixelSize: 12
                        font.weight: Font.Medium
                        wrapMode: Text.WordWrap
                    }

                    Button {
                        objectName: "retryDaemonButton"
                        visible: !sphere.serviceAvailable
                        text: qsTr("Retry now")
                        flat: true
                        onClicked: sphere.refresh()
                        Accessible.name: qsTr("Retry connection to the Sphere daemon")
                    }

                    Button {
                        visible: !!sphere.errorMessage && sphere.serviceAvailable
                        text: qsTr("Dismiss")
                        flat: true
                        onClicked: sphere.clearError()
                        Accessible.name: qsTr("Dismiss error")
                    }
                }

                Label {
                    objectName: "daemonRecoveryHint"
                    Layout.fillWidth: true
                    visible: !sphere.serviceAvailable && root.height >= 640
                    text: sphere.serviceState === "unresponsive"
                          ? qsTr("A command may still finish. The app will reconnect automatically; check the Sphere's state before repeating an action.")
                          : sphere.serviceState === "invalid_response"
                            ? qsTr("Check that the app and daemon are compatible and use the same service socket. Retrying automatically.")
                            : sphere.serviceState === "connecting"
                              ? qsTr("Checking the local service. Bluetooth controls will be enabled when it is ready.")
                              : qsTr("Make sure the Sphere daemon is running. The app will reconnect automatically. Check the service socket in Settings if it stays unavailable.")
                    color: root.muted
                    font.pixelSize: 11
                    wrapMode: Text.WordWrap
                }

                Label {
                    Layout.fillWidth: true
                    visible: !sphere.serviceAvailable && !!sphere.serviceError && root.height >= 640
                    text: sphere.serviceError
                    color: root.muted
                    font.pixelSize: 10
                    wrapMode: Text.WrapAnywhere
                }

                Label {
                    visible: !!sphere.errorMessage && root.readableDetails() !== ""
                    Layout.fillWidth: true
                    text: root.readableDetails()
                    color: root.muted
                    font.pixelSize: 10
                    font.family: "monospace"
                    wrapMode: Text.WrapAnywhere
                    maximumLineCount: 4
                    elide: Text.ElideRight
                }

                Button {
                    visible: !!sphere.errorMessage && sphere.serviceAvailable
                    text: qsTr("Refresh state")
                    flat: true
                    onClicked: sphere.refresh()
                }
            }
        }

        ColumnLayout {
            objectName: "sharedLightControls"
            Layout.fillWidth: true
            Layout.preferredHeight: visible ? implicitHeight : 0
            Layout.bottomMargin: visible ? 12 : 0
            spacing: 12

            Rectangle {
                Layout.fillWidth: true
                objectName: "globalPowerCard"
                Layout.preferredHeight: root.height < 650 ? 76 : 92
                radius: 16
                color: root.cardColor
                border.width: 1
                border.color: root.borderColor

                RowLayout {
                    anchors.fill: parent
                    anchors.margins: root.height < 650 ? 10 : 16

                    Item {
                        objectName: "mainPreview"
                        Layout.preferredWidth: root.width < 380 ? 48 : 60
                        Layout.preferredHeight: Layout.preferredWidth
                        readonly property var media: root.currentEffectMedia()
                        readonly property bool showEffect: sphere.connected && sphere.power === true
                                                           && sphere.effectActive
                                                           && (!!media.stillUrl || !!media.animationUrl)

                        Rectangle {
                            objectName: "mainPreviewOff"
                            anchors.fill: parent
                            visible: sphere.power === false
                            color: "black"
                        }

                        SpherePreview {
                            objectName: "mainPreviewIllustration"
                            anchors.fill: parent
                            visible: sphere.power !== false && (!parent.showEffect || !mainRecording.ready)
                            sphereColor: sphere.previewKind === "unknown"
                                         ? root.muted : root.previewColor
                            active: sphere.power === true && sphere.connected
                                    && sphere.previewKind !== "unknown"
                        }

                        RecordedPreview {
                            id: mainRecording
                            objectName: "mainRecordedPreview"
                            anchors.fill: parent
                            visible: parent.showEffect
                            stillUrl: visible ? parent.media.stillUrl || "" : ""
                            animationUrl: visible ? parent.media.animationUrl || "" : ""
                            animate: visible
                            gain: root.effectPreviewBrightness(root.currentPreviewBrightness)
                        }
                    }

                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 3

                        Label {
                            text: qsTr("Power")
                            color: root.foreground
                            font.pixelSize: 15
                            font.weight: Font.DemiBold
                        }

                        Label {
                            objectName: "sphereStateLabel"
                            Layout.fillWidth: true
                            text: !sphere.connected || sphere.power !== true ? qsTr("Light off")
                                  : (sphere.effectActive || sphere.colorModeActive) && root.effectLabel() !== ""
                                    ? (sphere.previewStateSource === "saved"
                                       ? qsTr("Last known: %1").arg(root.effectLabel()) : root.effectLabel())
                                  : sphere.workMode === 2 ? qsTr("Scene mode")
                                  : sphere.workMode === 3 ? qsTr("Music mode")
                                  : sphere.workMode === 4 ? qsTr("Mode 4")
                                  : sphere.previewKind === "unknown" ? qsTr("Scene unavailable") : qsTr("Light on")
                            elide: Text.ElideRight
                            color: root.muted
                            font.pixelSize: 11
                        }
                    }

                    Button {
                        objectName: "powerButton"
                        text: sphere.power ? qsTr("Turn off") : qsTr("Turn on")
                        enabled: root.controlsReady && root.capability("power")
                        highlighted: !sphere.power
                        onClicked: sphere.setPower(!sphere.power)
                        Accessible.name: text
                    }
                }
            }

            Rectangle {
                visible: root.currentPage === 0 || root.currentPage === 1
                Layout.fillWidth: true
                Layout.preferredHeight: visible ? (root.height < 650 ? 82 : 95) : 0
                radius: 16
                color: root.cardColor
                border.width: 1
                border.color: root.borderColor

                ControlSlider {
                    id: brightnessSlider
                    objectName: "brightnessSlider"
                    anchors.fill: parent
                    anchors.margins: root.height < 650 ? 10 : 16
                    title: qsTr("Brightness")
                    valueText: Math.round(previewValue) + "%"
                    sourceValue: root.brightnessPosition(
                                     sphere.effectActive || sphere.colorModeActive
                                     ? root.hsvValue("value") : sphere.lightness)
                    from: 0
                    to: 100
                    accent: root.accent
                    foreground: root.foreground
                    muted: root.muted
                    controlEnabled: root.controlsReady
                                    && ((sphere.effectActive || sphere.colorModeActive)
                                        ? root.capability("hsl") : root.capability("lightness"))
                    onCommitted: function(value) {
                        sphere.setBrightness(root.brightnessLightness(value))
                    }
                }
            }
        }

        StackLayout {
            id: pages
            objectName: "pages"
            Layout.fillWidth: true
            Layout.fillHeight: true
            currentIndex: root.currentPage

            ScrollView {
                id: lightScroll
                clip: true
                ScrollBar.horizontal.policy: ScrollBar.AlwaysOff

                ColumnLayout {
                    width: lightScroll.availableWidth
                    spacing: 12

                    RowLayout {
                        Layout.fillWidth: true
                        Layout.bottomMargin: 2

                        Label {
                            text: qsTr("Light")
                            color: root.foreground
                            font.pixelSize: 21
                            font.weight: Font.Bold
                            Layout.fillWidth: true
                        }

                        Label {
                            text: root.batteryKnown ? qsTr("Battery %1%").arg(sphere.battery) : ""
                            visible: text !== ""
                            color: root.muted
                            font.pixelSize: 12
                        }
                    }

                    Rectangle {
                        Layout.fillWidth: true
                        Layout.preferredHeight: 218
                        radius: 16
                        color: root.cardColor
                        border.width: 1
                        border.color: root.borderColor

                        ColumnLayout {
                            anchors.fill: parent
                            anchors.margins: 16
                            spacing: 4

                            RowLayout {
                                Layout.fillWidth: true

                                Button {
                                    objectName: "applyColorButton"
                                    text: qsTr("Color")
                                    flat: true
                                    highlighted: sphere.colorModeActive
                                    enabled: root.controlsReady && root.capability("hsl")
                                             && root.capability("effects")
                                    font.pixelSize: 15
                                    font.weight: Font.DemiBold
                                    onClicked: root.applySelectedColor()
                                    Accessible.name: qsTr("Apply selected solid color")
                                }

                                Item { Layout.fillWidth: true }

                                Rectangle {
                                    Layout.preferredWidth: 22
                                    Layout.preferredHeight: 22
                                    radius: 11
                                    color: root.previewColor
                                    border.width: 1
                                    border.color: root.borderColor
                                }
                            }

                            Label {
                                text: qsTr("Adjust hue and saturation")
                                color: root.muted
                                font.pixelSize: 11
                                Layout.bottomMargin: 4
                            }

                            ControlSlider {
                                id: hueSlider
                                Layout.fillWidth: true
                                title: qsTr("Hue")
                                valueText: Math.round(previewValue) + "°"
                                sourceValue: root.hsvValue("hue") * 360 / 65535
                                from: 0
                                to: 360
                                accent: root.accent
                                foreground: root.foreground
                                muted: root.muted
                                controlEnabled: root.controlsReady && root.capability("hsl")
                                onCommitted: function(value) {
                                    sphere.setHsv(root.brightnessLightness(brightnessSlider.previewValue),
                                                  Math.round(value * 65535 / 360),
                                                  Math.round(saturationSlider.previewValue * 65535 / 100))
                                }
                            }

                            ControlSlider {
                                id: saturationSlider
                                Layout.fillWidth: true
                                title: qsTr("Saturation")
                                valueText: Math.round(previewValue) + "%"
                                sourceValue: root.hsvValue("saturation") * 100 / 65535
                                from: 0
                                to: 100
                                accent: root.accent
                                foreground: root.foreground
                                muted: root.muted
                                controlEnabled: root.controlsReady && root.capability("hsl")
                                onCommitted: function(value) {
                                    sphere.setHsv(root.brightnessLightness(brightnessSlider.previewValue),
                                                  Math.round(hueSlider.previewValue * 65535 / 360),
                                                  Math.round(value * 65535 / 100))
                                }
                            }

                        }
                    }

                    Label {
                        Layout.fillWidth: true
                        Layout.bottomMargin: 10
                        text: qsTr("Changes are sent when you release a slider.")
                        color: root.muted
                        font.pixelSize: 11
                        wrapMode: Text.WordWrap
                    }
                }
            }

            ScrollView {
                id: effectsScroll
                objectName: "effectsScroll"
                clip: true
                ScrollBar.horizontal.policy: ScrollBar.AlwaysOff

                ColumnLayout {
                    width: effectsScroll.availableWidth
                    spacing: 12

                    Label {
                        text: qsTr("Effects")
                        color: root.foreground
                        font.pixelSize: 21
                        font.weight: Font.Bold
                    }

                    Label {
                        Layout.fillWidth: true
                        text: sphere.effectActive && root.effectLabel() !== ""
                              ? qsTr("Active: %1").arg(root.effectLabel())
                              : qsTr("Choose a built-in look for Sphere C")
                        color: root.muted
                        font.pixelSize: 12
                        wrapMode: Text.WordWrap
                    }

                    RowLayout {
                        Layout.fillWidth: true
                        Button {
                            objectName: "patternsSelector"
                            text: qsTr("Patterns")
                            checkable: true
                            autoExclusive: true
                            checked: root.effectCategory === "pattern"
                            Layout.fillWidth: true
                            Accessible.name: text
                            onClicked: root.effectCategory = "pattern"
                        }
                        Button {
                            objectName: "animationsSelector"
                            text: qsTr("Animations")
                            checkable: true
                            autoExclusive: true
                            checked: root.effectCategory === "animation"
                            Layout.fillWidth: true
                            Accessible.name: text
                            onClicked: root.effectCategory = "animation"
                        }
                    }

                    TextField {
                        id: effectSearch
                        objectName: "effectSearch"
                        Layout.fillWidth: true
                        placeholderText: qsTr("Search effects")
                        Accessible.name: qsTr("Search effects")
                        enabled: root.capability("effects")
                    }

                    Label {
                        Layout.fillWidth: true
                        visible: !root.capability("effects")
                        text: qsTr("Effects are unavailable on this device.")
                        color: root.muted
                        wrapMode: Text.WordWrap
                    }

                    GridLayout {
                        id: effectsGrid
                        objectName: "effectsGrid"
                        Layout.fillWidth: true
                        Layout.preferredHeight: implicitHeight
                        Layout.bottomMargin: 12
                        columns: width >= 540 ? 4 : width >= 360 ? 3 : 2
                        columnSpacing: 8
                        rowSpacing: 8

                        Repeater {
                            id: effectRepeater
                            objectName: "effectRepeater"
                            model: root.matchingEffects(effectSearch.text)

                            delegate: Item {
                                id: effectTile
                                objectName: "effectTile"
                                required property var modelData
                                readonly property string effectName: modelData.label || modelData.name || ""
                                readonly property string effectSlug: modelData.slug || ""
                                readonly property bool selected: (sphere.effect || ({})).slug === effectSlug
                                                                && (effectSlug === "color-wheel"
                                                                    ? sphere.colorModeActive
                                                                    : sphere.effectActive)

                                Layout.preferredWidth: (effectsGrid.width - (effectsGrid.columns - 1)
                                                        * effectsGrid.columnSpacing) / effectsGrid.columns
                                Layout.preferredHeight: Layout.preferredWidth + 44
                                Layout.fillWidth: true

                                Rectangle {
                                    anchors.fill: parent
                                    radius: 12
                                    color: selected
                                           ? (root.darkMode ? "#2d3b66" : "#eef0ff")
                                           : root.cardColor
                                    border.width: selected || effectTile.activeFocus ? 2 : 1
                                    border.color: effectTile.activeFocus ? root.foreground
                                                              : selected ? root.accent : root.borderColor

                                    Item {
                                        id: preview
                                        anchors.top: parent.top
                                        anchors.left: parent.left
                                        anchors.right: parent.right
                                        anchors.margins: 5
                                        height: width
                                        clip: true

                                        RecordedPreview {
                                            id: recordedPreview
                                            objectName: "effectPreviewRendering"
                                            anchors.fill: parent
                                            stillUrl: modelData.stillUrl || ""
                                            animationUrl: modelData.animationUrl || ""
                                            animate: root.currentPage === 1
                                                     && (tileMouse.containsMouse || effectTile.activeFocus || selected)
                                            gain: root.effectPreviewBrightness(50)
                                        }

                                        Label {
                                            objectName: "previewFallback"
                                            z: 1
                                            anchors.centerIn: parent
                                            visible: !recordedPreview.ready
                                            text: qsTr("Preview unavailable")
                                            color: root.muted
                                            font.pixelSize: 11
                                        }

                                    }

                                    Label {
                                        anchors.top: preview.bottom
                                        anchors.bottom: parent.bottom
                                        anchors.left: parent.left
                                        anchors.right: parent.right
                                        anchors.margins: 4
                                        text: effectName
                                        color: root.foreground
                                        font.pixelSize: 13
                                        font.weight: selected ? Font.Bold : Font.DemiBold
                                        horizontalAlignment: Text.AlignHCenter
                                        verticalAlignment: Text.AlignVCenter
                                        maximumLineCount: 2
                                        elide: Text.ElideRight
                                        wrapMode: Text.WordWrap
                                    }

                                    MouseArea {
                                        id: tileMouse
                                        anchors.fill: parent
                                        hoverEnabled: true
                                        enabled: root.controlsReady && root.capability("effects")
                                        cursorShape: enabled ? Qt.PointingHandCursor : Qt.ArrowCursor
                                        onClicked: root.selectEffect(effectSlug)
                                    }
                                }

                                Keys.onReturnPressed: {
                                    if (root.controlsReady && root.capability("effects"))
                                        root.selectEffect(effectSlug)
                                }

                                Keys.onSpacePressed: {
                                    if (root.controlsReady && root.capability("effects"))
                                        root.selectEffect(effectSlug)
                                }

                                activeFocusOnTab: visible && root.controlsReady && root.capability("effects")
                                Accessible.role: Accessible.Button
                                Accessible.name: effectName
                                Accessible.description: selected ? qsTr("Selected effect") : qsTr("Activate effect")
                            }
                        }
                    }

                    Label {
                        Layout.fillWidth: true
                        visible: root.capability("effects") && sphere.effects.length === 0
                        text: qsTr("Effect catalog is loading…")
                        color: root.muted
                    }
                }
            }

            ScrollView {
                id: settingsScroll
                clip: true
                ScrollBar.horizontal.policy: ScrollBar.AlwaysOff

                ColumnLayout {
                    width: settingsScroll.availableWidth
                    spacing: 12

                    Label {
                        text: qsTr("Settings")
                        color: root.foreground
                        font.pixelSize: 21
                        font.weight: Font.Bold
                    }

                    Rectangle {
                        Layout.fillWidth: true
                        Layout.preferredHeight: detailsColumn.implicitHeight + 32
                        radius: 16
                        color: root.cardColor
                        border.width: 1
                        border.color: root.borderColor

                        ColumnLayout {
                            id: detailsColumn
                            anchors.left: parent.left
                            anchors.right: parent.right
                            anchors.verticalCenter: parent.verticalCenter
                            anchors.leftMargin: 16
                            anchors.rightMargin: 16
                            spacing: 12

                            Label {
                                text: qsTr("Connection")
                                color: root.foreground
                                font.pixelSize: 15
                                font.weight: Font.DemiBold
                            }

                            Label {
                                Layout.fillWidth: true
                                text: qsTr("State: %1").arg(sphere.connectionState || qsTr("unavailable"))
                                color: root.muted
                                wrapMode: Text.WrapAnywhere
                            }

                            Label {
                                Layout.fillWidth: true
                                text: qsTr("Socket: %1").arg(sphere.socketPath || qsTr("unavailable"))
                                color: root.muted
                                wrapMode: Text.WrapAnywhere
                            }

                            Label {
                                Layout.fillWidth: true
                                text: qsTr("Generation: %1").arg(sphere.generation)
                                color: root.muted
                            }

                            Label {
                                Layout.fillWidth: true
                                visible: !!sphere.lastFailure
                                text: qsTr("Last failure: %1").arg(sphere.lastFailure)
                                color: root.danger
                                wrapMode: Text.WrapAnywhere
                            }
                        }
                    }

                    Rectangle {
                        Layout.fillWidth: true
                        Layout.preferredHeight: capabilityColumn.implicitHeight + 32
                        radius: 16
                        color: root.cardColor
                        border.width: 1
                        border.color: root.borderColor

                        ColumnLayout {
                            id: capabilityColumn
                            anchors.left: parent.left
                            anchors.right: parent.right
                            anchors.verticalCenter: parent.verticalCenter
                            anchors.leftMargin: 16
                            anchors.rightMargin: 16
                            spacing: 8

                            Label {
                                text: qsTr("Available controls")
                                color: root.foreground
                                font.pixelSize: 15
                                font.weight: Font.DemiBold
                            }

                            Label {
                                Layout.fillWidth: true
                                text: [qsTr("Power"), qsTr("Brightness"), qsTr("Color"),
                                       qsTr("Effects")].filter(function(_, index) {
                                           return root.capability(["power", "lightness", "hsl",
                                                                   "effects"][index])
                                       }).join(" · ") || qsTr("None reported")
                                color: root.muted
                                wrapMode: Text.WordWrap
                            }

                            Label {
                                visible: root.batteryKnown
                                text: qsTr("Battery: %1%").arg(sphere.battery)
                                color: root.muted
                            }
                        }
                    }

                    RowLayout {
                        Layout.fillWidth: true
                        Layout.bottomMargin: 12

                        Button {
                            Layout.fillWidth: true
                            text: qsTr("Refresh")
                            enabled: !sphere.pending
                            onClicked: sphere.refresh()
                        }

                        Button {
                            Layout.fillWidth: true
                            text: qsTr("Copy diagnostics")
                            onClicked: sphere.copyDiagnostics()
                        }
                    }

                    Label {
                        Layout.fillWidth: true
                        visible: !sphere.connected
                        text: qsTr("Start the Sphere service to reconnect. The app will update automatically when it becomes available.")
                        color: root.muted
                        wrapMode: Text.WordWrap
                    }
                }
            }
        }

        Rectangle {
            Layout.fillWidth: true
            Layout.preferredHeight: 62
            Layout.topMargin: 10
            radius: 17
            color: root.cardColor
            border.width: 1
            border.color: root.borderColor

            RowLayout {
                anchors.fill: parent
                anchors.margins: 5
                spacing: 5

                Repeater {
                    model: [qsTr("Light"), qsTr("Effects"), qsTr("Settings")]

                    delegate: Button {
                        required property int index
                        required property string modelData

                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        text: modelData
                        checked: root.currentPage === index
                        checkable: true
                        flat: !checked
                        onClicked: root.currentPage = index
                        Accessible.name: modelData + qsTr(" tab")
                        Accessible.description: checked ? qsTr("Selected") : ""
                    }
                }
            }
        }
    }
    ManageSpheresDialog {
        id: manageDialog
        backend: sphere
        foreground: root.foreground
        muted: root.muted
        cardColor: root.cardColor
        borderColor: root.borderColor
        danger: root.danger
    }
}
