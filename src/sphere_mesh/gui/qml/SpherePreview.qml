import QtQuick

Item {
    id: root

    property color sphereColor: "#7784e9"
    property bool active: false

    implicitWidth: 60
    implicitHeight: 60

    Rectangle {
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.verticalCenter: parent.verticalCenter
        width: parent.width
        height: width
        radius: width / 2
        color: root.sphereColor
        opacity: root.active ? 0.09 : 0.04
    }

    Rectangle {
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.verticalCenter: parent.verticalCenter
        width: parent.width * 0.8
        height: width
        radius: width / 2
        color: root.sphereColor
        opacity: root.active ? 0.17 : 0.07
    }

    Rectangle {
        id: orb
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.verticalCenter: parent.verticalCenter
        width: parent.width * 0.64
        height: width
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
            x: parent.width * 0.18
            y: parent.height * 0.14
            width: parent.width * 0.36
            height: parent.height * 0.17
            radius: height / 2
            rotation: -28
            color: "white"
            opacity: root.active ? 0.25 : 0.2
        }

        Rectangle {
            anchors.horizontalCenter: parent.horizontalCenter
            y: parent.height * 0.72
            width: parent.width * 0.55
            height: Math.max(1, parent.height * 0.017)
            radius: 1
            color: "white"
            opacity: 0.24
        }
    }

}
