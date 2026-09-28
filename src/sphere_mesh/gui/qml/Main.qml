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
    title: qsTr("Sphere C")

    property int currentPage: 0
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
                                                    root.previewBrightness(brightnessSlider.previewValue), 1)
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

    function effectLabel() {
        var selection = sphere.effect || ({})
        return selection.label || ""
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
            if (effect.visible !== false && label.toLowerCase().indexOf(needle) !== -1)
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
                    text: qsTr("Sphere C")
                    color: root.foreground
                    font.pixelSize: 22
                    font.weight: Font.Bold
                }

                Label {
                    text: qsTr("Your light, your mood")
                    color: root.muted
                    font.pixelSize: 11
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
                    anchors.centerIn: parent
                    text: sphere.connected ? qsTr("Connected")
                           : sphere.connectionState === "reconnecting" ? qsTr("Reconnecting")
                           : sphere.connectionState === "connecting" ? qsTr("Connecting")
                           : qsTr("Offline")
                    color: sphere.connected ? root.success : root.muted
                    font.pixelSize: 11
                    font.weight: Font.DemiBold
                }
            }
        }

        SpherePreview {
            Layout.fillWidth: true
            Layout.preferredHeight: root.height < 650 ? 110 : 184
            sphereColor: root.previewColor
            active: sphere.power === true && sphere.connected
            effectLabel: sphere.effectActive || sphere.colorModeActive
                         ? root.effectLabel() : ""
            foreground: root.foreground
            muted: root.muted
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
                        Layout.fillWidth: true
                        text: sphere.errorMessage ? sphere.errorMessage
                              : sphere.pending ? (sphere.pendingAction || qsTr("Updating Sphere…"))
                              : qsTr("Waiting for the Sphere service")
                        color: sphere.errorMessage ? root.danger : root.foreground
                        font.pixelSize: 12
                        font.weight: Font.Medium
                        wrapMode: Text.WordWrap
                    }

                    Button {
                        visible: !!sphere.errorMessage
                        text: qsTr("Dismiss")
                        flat: true
                        onClicked: sphere.clearError()
                        Accessible.name: qsTr("Dismiss error")
                    }
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
                    visible: !!sphere.errorMessage
                    text: qsTr("Refresh state")
                    flat: true
                    onClicked: sphere.refresh()
                }
            }
        }

        ColumnLayout {
            objectName: "sharedLightControls"
            visible: root.currentPage === 0 || root.currentPage === 1
            Layout.fillWidth: true
            Layout.preferredHeight: visible ? implicitHeight : 0
            Layout.bottomMargin: visible ? 12 : 0
            spacing: 12

            Rectangle {
                Layout.fillWidth: true
                Layout.preferredHeight: root.height < 650 ? 66 : 74
                radius: 16
                color: root.cardColor
                border.width: 1
                border.color: root.borderColor

                RowLayout {
                    anchors.fill: parent
                    anchors.margins: root.height < 650 ? 10 : 16

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
                            text: !root.capability("power") ? qsTr("Unavailable on this device")
                                  : sphere.power ? qsTr("Sphere is on") : qsTr("Sphere is off")
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
                Layout.fillWidth: true
                Layout.preferredHeight: root.height < 650 ? 82 : 95
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
                        text: qsTr("Choose a built-in look for Sphere C")
                        color: root.muted
                        font.pixelSize: 12
                        wrapMode: Text.WordWrap
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
                                Layout.preferredHeight: 60
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

                                    Label {
                                        anchors.fill: parent
                                        anchors.margins: 8
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
                                        anchors.fill: parent
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
                                text: qsTr("Node: %1").arg(sphere.device || qsTr("unavailable"))
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
}
