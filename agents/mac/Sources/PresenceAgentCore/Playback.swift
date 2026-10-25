import Foundation

/// 音乐、视频这类有进度条的播放状态。
public protocol PlaybackState: Equatable, Sendable {
    /// 变了就立即上报（换歌、换视频、封面传好等）
    var changeKey: String { get }
    var playing: Bool { get set }
    var position: Double { get set }
    var duration: Double { get }
    /// 播放倍速，用来推算进度
    var rate: Double { get }
    func signal(ts: Double, ttl: Double) -> Signal
}

public extension PlaybackState {
    var rate: Double { 1 }
}

/// 决定什么时候上报：换歌 / 换视频、暂停 / 播放、拖动进度时立即发；
/// 播放中按心跳间隔刷新；暂停或退出后只发一次（服务器保留 1 分钟余温后自己结束）。
public struct PlaybackPolicy<State: PlaybackState>: Sendable {
    /// 实际进度和按时间推算的进度相差超过这么多秒，就当作拖动了进度条
    public var seekToleranceSec: Double = 3
    public private(set) var last: State?
    public private(set) var lastSent: Double?

    public init() {}

    /// state 为 nil 表示没在放。返回需要上报的信号，不需要时返回 nil。
    public mutating func signal(for state: State?, now: Double, heartbeatSec: Double, ttl: Double) -> Signal? {
        guard let state else {
            // 刚才还在放：补一条“已暂停”，网页上显示 1 分钟后消失
            guard var prev = last, prev.playing else { return nil }
            if let sent = lastSent { prev.position = min(prev.position + (now - sent) * prev.rate, prev.duration) }
            prev.playing = false
            return record(prev, now: now, ttl: ttl)
        }
        guard let prev = last, let sent = lastSent else { return record(state, now: now, ttl: ttl) }
        let changed = state.changeKey != prev.changeKey || state.playing != prev.playing || state.rate != prev.rate
        let expected = prev.playing ? prev.position + (now - sent) * prev.rate : prev.position
        let seeked = abs(state.position - expected) > seekToleranceSec
        let heartbeat = state.playing && now - sent >= heartbeatSec
        return changed || seeked || heartbeat ? record(state, now: now, ttl: ttl) : nil
    }

    public mutating func reset() {
        last = nil
        lastSent = nil
    }

    private mutating func record(_ state: State, now: Double, ttl: Double) -> Signal {
        last = state
        lastSent = now
        return state.signal(ts: now, ttl: ttl)
    }
}
