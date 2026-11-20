// Apple Vision reads local pixels. This script does not control any applications.
ObjC.import('Vision')
function run(argv) {
    const request = $.VNRecognizeTextRequest.alloc.init
    request.recognitionLevel = 0 // VNRequestTextRecognitionLevelAccurate
    request.recognitionLanguages = ['ja-JP', 'en-US']
    request.usesLanguageCorrection = false
    const handler = $.VNImageRequestHandler.alloc.initWithURLOptions($.NSURL.fileURLWithPath(argv[0]), $({}))
    if (!handler.performRequestsError($.NSArray.arrayWithObject(request), Ref())) throw Error('OCR unavailable')
    return ObjC.unwrap(request.results).map(row => ObjC.unwrap(row.topCandidates(1).firstObject.string)).join('\n')
}
