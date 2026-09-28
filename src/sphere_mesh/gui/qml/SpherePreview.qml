import QtQuick
import QtQuick.Controls

Item {
    id: root

    property color sphereColor: "#7784e9"
    property bool active: false
    property string effectLabel: ""
    property color foreground: "#17243b"
    property color muted: "#62718a"

    implicitHeight: 184

    Rectangle {
        anchors.horizontalCenter: parent.horizontalCenter
        y: 12
        width: 182
        height: 182
        radius: width / 2
        color: root.sphereColor
        opacity: root.active ? 0.09 : 0.04
    }

    Rectangle {
        anchors.horizontalCenter: parent.horizontalCenter
        y: 30
        width: 146
        height: 146
        radius: width / 2
        color: root.sphereColor
        opacity: root.active ? 0.17 : 0.07
    }

    Rectangle {
        id: orb
        anchors.horizontalCenter: parent.horizontalCenter
        y: 46
        width: 116
        height: 116
        radius: width / 2
        color: root.active ? root.sphereColor : "#9eaab9"
        border.color: root.active ? Qt.lighter(root.sphereColor, 1.35) : "#cad2df"
        border.width: 2

        gradient: Gradient {
            GradientStop {
                position: 0
                color: root.active ? Qt.lighter(root.sphereColor, 1.8) : "#dce2eb"
            }
            GradientStop {
                position: 0.55
                color: root.active ? Qt.lighter(root.sphereColor, 1.16) : "#aeb9c8"
            }
            GradientStop {
                position: 1
                color: root.active ? Qt.darker(root.sphereColor, 1.45) : "#8391a3"
            }
        }

        Rectangle {
            x: 21
            y: 16
            width: 42
            height: 20
            radius: height / 2
            rotation: -28
            color: "white"
            opacity: root.active ? 0.25 : 0.2
        }

        Rectangle {
            anchors.horizontalCenter: parent.horizontalCenter
            y: 83
            width: 64
            height: 2
            radius: 1
            color: "white"
            opacity: 0.24
        }
    }

    Label {
        objectName: "sphereStateLabel"
        anchors.horizontalCenter: parent.horizontalCenter
        y: 168
        text: root.active ? (root.effectLabel ? root.effectLabel : qsTr("Light on")) : qsTr("Light off")
        color: root.active ? root.foreground : root.muted
        font.pixelSize: 13
        font.weight: Font.Medium
    }
}
