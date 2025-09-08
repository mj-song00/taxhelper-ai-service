package texhelper.chatservice.common.response;

import lombok.Getter;
import org.springframework.http.HttpStatus;
import texhelper.chatservice.common.exception.ExceptionEnum;

@Getter
public class ApiResponse<T> {

    private final int statusCode;
    private final String message;
    private final T data;

    // 성공 응답 생성자
    public ApiResponse(T data, ApiResponseEnum messageEnum) {
        this.statusCode = HttpStatus.OK.value();
        this.message = messageEnum.getMessage();
        this.data = data;
    }

    // 에러 응답 생성자 (필드별 오류 메시지 포함)
    public ApiResponse(ExceptionEnum exceptionEnum, HttpStatus status, T data) {
        this.statusCode = status.value();
        this.message = exceptionEnum.getMessage();
        this.data = data;  // 유효성 검사 실패 시 필드별 오류 메시지
    }

    // 성공 응답 메서드 (데이터 있을 때)
    public static <T> ApiResponse<T> successWithData(T data, ApiResponseEnum messageEnum) {
        return new ApiResponse<>(data, messageEnum);
    }

    // 성공 응답 메서드 (데이터 없이)
    public static <T> ApiResponse<T> successWithOutData(ApiResponseEnum messageEnum) {
        return new ApiResponse<>(null, messageEnum);
    }

    // 에러 응답 메서드 (데이터 있을 때)
    public static ApiResponse<?> errorWithData(ExceptionEnum exceptionEnum, HttpStatus status,
                                               Object data) {
        return new ApiResponse<>(exceptionEnum, status, data);
    }

    // 에러 응답 메서드 (데이터 없이)
    public static ApiResponse<?> errorWithOutData(ExceptionEnum exceptionEnum, HttpStatus status) {
        return new ApiResponse<>(exceptionEnum, status, null);
    }

}
