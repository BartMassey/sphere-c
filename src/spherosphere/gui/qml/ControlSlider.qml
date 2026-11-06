import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

ColumnLayout {
    id: root

    property string title: ""
    property string valueText: ""
    property real sourceValue: 0
    property real from: 0
    property real to: 100
    property real stepSize: 1
    property color accent: "#6575e8"
    property color foreground: "#17243b"
    property color muted: "#62718a"
    property bool controlEnabled: true
    property real previewValue: slider.value

    signal committed(real value)

    spacing: 6

    onSourceValueChanged: {
        if (!slider.pressed)
            slider.value = sourceValue
    }

    RowLayout {
        Layout.fillWidth: true

        Label {
            text: root.title
            color: root.controlEnabled ? root.foreground : root.muted
            font.pixelSize: 14
            font.weight: Font.DemiBold
            Layout.fillWidth: true
        }

        Label {
            text: root.valueText
            color: root.muted
            font.pixelSize: 13
        }
    }

    Slider {
        id: slider

        Layout.fillWidth: true
        from: root.from
        to: root.to
        stepSize: root.stepSize
        value: root.sourceValue
        enabled: root.controlEnabled
        focusPolicy: Qt.StrongFocus
        Accessible.name: root.title
        Accessible.description: root.valueText

        onMoved: {
            if (!pressed)
                root.committed(value)
        }

        onPressedChanged: {
            if (!pressed && enabled && Math.abs(value - root.sourceValue) > 0.001)
                root.committed(value)
        }

        background: Rectangle {
            x: slider.leftPadding
            y: slider.topPadding + slider.availableHeight / 2 - height / 2
            width: slider.availableWidth
            height: 8
            radius: 4
            color: root.controlEnabled ? "#dce4ef" : "#e8ebf0"

            Rectangle {
                width: slider.visualPosition * parent.width
                height: parent.height
                radius: parent.radius
                color: root.controlEnabled ? root.accent : "#b1bac9"
            }
        }

        handle: Rectangle {
            x: slider.leftPadding + slider.visualPosition * (slider.availableWidth - width)
            y: slider.topPadding + slider.availableHeight / 2 - height / 2
            implicitWidth: 22
            implicitHeight: 22
            radius: width / 2
            color: "white"
            border.width: 3
            border.color: root.controlEnabled ? root.accent : "#b1bac9"
        }
    }
}
