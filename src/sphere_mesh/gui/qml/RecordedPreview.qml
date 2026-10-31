import QtQuick

Item {
    id: root

    property url stillUrl: ""
    property url animationUrl: ""
    property bool animate: false
    property real gain: 1
    readonly property bool ready: stillImage.status === Image.Ready
                                  || (animationLoader.item !== null
                                      && animationLoader.item.status === Image.Ready)

    clip: true

    Item {
        anchors.fill: parent
        layer.enabled: GraphicsInfo.api !== GraphicsInfo.Software
        layer.effect: ShaderEffect {
            property var source
            property real gain: root.gain
            fragmentShader: "shaders/preview-brightness.frag.qsb"
        }

        Image {
            id: stillImage
            objectName: "effectStill"
            anchors.fill: parent
            source: root.stillUrl
            fillMode: Image.PreserveAspectCrop
            asynchronous: true
        }

        Loader {
            id: animationLoader
            objectName: "effectAnimationLoader"
            anchors.fill: parent
            active: root.visible && root.animate && root.animationUrl.toString() !== ""
            sourceComponent: AnimatedImage {
                objectName: "recordedAnimation"
                source: root.animationUrl
                fillMode: Image.PreserveAspectCrop
                playing: true
                cache: false
            }
        }
    }

    Rectangle {
        anchors.fill: parent
        visible: GraphicsInfo.api === GraphicsInfo.Software
        color: "black"
        opacity: Math.max(0, 1 - root.gain)
    }
}
