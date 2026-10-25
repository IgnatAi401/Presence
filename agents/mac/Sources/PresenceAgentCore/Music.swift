import CryptoKit
import Foundation
import ImageIO
import UniformTypeIdentifiers

/// “音乐” App 此刻的播放状态。
public struct MusicState: PlaybackState {
    public var trackId: String
    public var title: String
    public var artist: String
    public var album: String
    public var duration: Double
    public var position: Double
    public var playing: Bool
    /// 封面图片的 sha256；服务器确认收到之前为 nil
    public var coverHash: String?

    public init(trackId: String, title: String, artist: String = "", album: String = "", duration: Double = 0,
                position: Double = 0, playing: Bool, coverHash: String? = nil) {
        self.trackId = trackId
        self.title = title
        self.artist = artist
        self.album = album
        self.duration = duration
        self.position = position
        self.playing = playing
        self.coverHash = coverHash
    }

    /// 换歌或封面传好时立即上报
    public var changeKey: String { [trackId, title, coverHash ?? ""].joined(separator: "\u{1F}") }

    public func signal(ts: Double, ttl: Double) -> Signal {
        var data: [String: JSONValue] = [
            "player": .string("apple_music"),
            "title": .string(title),
            "artist": .string(artist),
            "album": .string(album),
            "state": .string(playing ? "playing" : "paused"),
            "position": .number((position * 10).rounded() / 10),
            "duration": .number((duration * 10).rounded() / 10),
        ]
        if let coverHash { data["cover_hash"] = .string(coverHash) }
        return Signal(kind: "music", ts: ts, ttl: ttl, data: data)
    }
}

public typealias MusicPolicy = PlaybackPolicy<MusicState>

/// 封面图片：判断格式、算哈希。
public enum CoverImage {
    /// 服务器接受的格式，按文件头判断
    public static func mimeType(of data: Data) -> String? {
        let b = [UInt8](data.prefix(12))
        if b.starts(with: [0xFF, 0xD8, 0xFF]) { return "image/jpeg" }
        if b.starts(with: [0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A]) { return "image/png" }
        if b.count == 12, b[0 ..< 4].elementsEqual("RIFF".utf8), b[8 ..< 12].elementsEqual("WEBP".utf8) {
            return "image/webp"
        }
        return nil
    }

    /// 整理成要上传的图片：本来就是 JPEG / PNG / WebP 且不大时原样使用；
    /// 否则缩到最长边 maxPixel 并转成 JPEG。读不出图片时返回 nil。
    public static func prepare(_ raw: Data, maxBytes: Int = 400_000, maxPixel: Int = 600) -> Data? {
        if mimeType(of: raw) != nil, raw.count <= maxBytes { return raw }
        guard let source = CGImageSourceCreateWithData(raw as CFData, nil),
              let image = CGImageSourceCreateThumbnailAtIndex(source, 0, [
                  kCGImageSourceCreateThumbnailFromImageAlways: true,
                  kCGImageSourceCreateThumbnailWithTransform: true,
                  kCGImageSourceThumbnailMaxPixelSize: maxPixel,
              ] as CFDictionary)
        else { return nil }
        let out = NSMutableData()
        guard let dest = CGImageDestinationCreateWithData(out, UTType.jpeg.identifier as CFString, 1, nil) else {
            return nil
        }
        CGImageDestinationAddImage(dest, image, [kCGImageDestinationLossyCompressionQuality: 0.85] as CFDictionary)
        return CGImageDestinationFinalize(dest) ? out as Data : nil
    }

    public static func sha256(_ data: Data) -> String {
        SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
    }
}
